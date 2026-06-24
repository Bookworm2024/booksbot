"""
handlers/indexer.py — real-time file indexing from the file channel.

When a new file is posted to the live file channel, the bot (a member of it)
receives it as a channel_post, extracts a clean title + ids, and upserts it into
the `files` collection. Because this comes through the Bot API, file_id IS
bot-usable — but we deliver via copy_message(msg_id) anyway so the same path
works for Telethon-backfilled files too.

It also services the WATCHLIST: when a newly indexed title matches a user's
earlier "not found" search, that user is DM'd that their book has arrived.
"""
import logging
import re

from aiogram import Router
from aiogram.types import Message

from database.connection import MongoManager
from utils.channel import get_file_channel
from utils.files import extract_from_message, index_file
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _norm(text: str) -> str:
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


# No constant-bound magic filter here: the file channel is now a LIVE setting, so
# we observe every channel_post and compare against the current id at call time.
# (The indexer router is included last and nothing else consumes channel_post.)
@router.channel_post()
async def on_file_post(message: Message) -> None:
    live = await get_file_channel()
    if not live or message.chat.id != live:
        return
    item = extract_from_message(message)
    if not item:
        return
    created = await index_file(item)
    if created:
        logger.info("Indexed new file: %s", item["name"])
        await _service_watchlist(message.bot, item["name"])


async def _service_watchlist(bot, new_title: str) -> None:
    """Notify users whose watch query matches the freshly indexed title."""
    db = await MongoManager.get()
    norm_title = _norm(new_title)
    if not norm_title:
        return
    watchers = await db.find_global("watchlist", {"matched": False},
                                    proj={"user_id": 1, "query": 1, "query_norm": 1})
    for w in watchers:
        words = (w.get("query_norm") or "").split()
        if words and all(word in norm_title for word in words):
            try:
                await bot.send_message(
                    w["user_id"],
                    "✨ <b>Watchlist Match!</b>\n\n"
                    f"The book you wanted — <code>{w.get('query')}</code> — is now "
                    "in our archive!",
                    reply_markup=kb([btn("🔍 Search & Download", "menu_request",
                                         style="success")]),
                )
            except Exception:  # noqa: BLE001 — user may have blocked the bot
                pass
            await db.safe_update("watchlist",
                                 {"user_id": w["user_id"], "query_norm": w.get("query_norm")},
                                 {"$set": {"matched": True}}, upsert=False)
