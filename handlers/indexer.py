"""
handlers/indexer.py — real-time file indexing from the file channel.

Whatever file lands in the live file channel — a direct upload, a forward from a
bot, an album item, a photo, an edited post, anything — the bot (an ADMIN of the
channel, so it receives channel_post) extracts a clean title + ids and upserts it
into the `files` collection. Both new posts (channel_post) AND edits
(edited_channel_post) are indexed; index_file dedupes on (chan_id, msg_id) so
re-seeing a message is a harmless no-op. Because this comes through the Bot API,
file_id IS bot-usable — but we deliver via copy_message(msg_id) anyway so the same
path works for Telethon-backfilled files too.

NOTE (operational): the bot must be an *administrator* of the file channel to
receive channel_post updates at all, and the live channel id must be set (Admin →
🗂 File Channel). Neither is a code concern — this handler indexes every file once
those hold.

It also services the WATCHLIST: when a newly indexed title matches a user's
earlier "not found" search, that user is DM'd that their book has arrived.
"""
import logging
import re
from html import escape

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
async def _index_post(message: Message) -> None:
    live = await get_file_channel()
    if not live or message.chat.id != live:
        return
    # Skip the prep pipeline's own re-uploads (prepared/staging copies of files that
    # are already indexed) — they carry an invisible marker in the caption.
    from utils.prepare import PREP_MARKER
    if (message.caption or "").startswith(PREP_MARKER):
        return
    item = extract_from_message(message)
    if not item:
        return  # not a file-bearing post (e.g. a plain text announcement)
    created = await index_file(item)
    if created:
        logger.info("Indexed new file: %s (%s)", item["name"], item.get("kind"))
        await _service_watchlist(message.bot, item["name"])


@router.channel_post()
async def on_file_post(message: Message) -> None:
    await _index_post(message)


@router.edited_channel_post()
async def on_file_edit(message: Message) -> None:
    # an edit can add a file/caption to a post we hadn't indexed yet; re-running is
    # safe because index_file dedupes on (chan_id, msg_id).
    await _index_post(message)


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
                    f"The book you wanted — <code>{escape(w.get('query') or '')}</code> — is now "
                    "in our archive!",
                    reply_markup=kb([btn("🔍 Search & Download", "menu_request",
                                         style="success")]),
                )
            except Exception:  # noqa: BLE001 — user may have blocked the bot
                pass
            await db.safe_update("watchlist",
                                 {"user_id": w["user_id"], "query_norm": w.get("query_norm")},
                                 {"$set": {"matched": True}}, upsert=False)
