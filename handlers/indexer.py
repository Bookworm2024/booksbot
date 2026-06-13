"""
handlers/indexer.py — real-time file indexing from the file channel.

When a new file is posted to FILE_CHANNEL_ID, the bot (a member of that channel)
receives it as a channel_post, extracts a clean title + ids, and upserts it into
the `files` collection. Because this comes through the Bot API, file_id IS
bot-usable — but we deliver via copy_message(msg_id) anyway so the same path
works for Telethon-backfilled files too.

It also services the WATCHLIST: when a newly indexed title matches a user's
earlier "not found" search, that user is DM'd that their book has arrived.
"""
import logging
import re

from aiogram import F, Router
from aiogram.types import Message

from config import FILE_CHANNEL_ID
from database.connection import MongoManager
from utils.files import clean_title, index_file, kind_for_ext
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _norm(text: str) -> str:
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


def _extract(message: Message) -> dict | None:
    """Build a `files` doc from a channel post, or None if it carries no file."""
    raw_name = ""
    file_id = file_uid = None
    kind = "document"

    if message.document:
        d = message.document
        raw_name = d.file_name or ""
        file_id, file_uid = d.file_id, d.file_unique_id
        kind = "document"
    elif message.audio:
        a = message.audio
        raw_name = a.file_name or a.title or ""
        file_id, file_uid = a.file_id, a.file_unique_id
        kind = "audio"
    elif message.video:
        v = message.video
        raw_name = v.file_name or ""
        file_id, file_uid = v.file_id, v.file_unique_id
        kind = "video"
    else:
        return None

    if not raw_name:
        raw_name = (message.caption or "").split("\n")[0]
    if not raw_name:
        return None

    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else ""
    name = clean_title(raw_name)
    return {
        "file_unique_id": file_uid or str(message.message_id),
        "name": name,
        "name_lc": name.lower(),
        "ext": ext,
        "kind": kind if message.video else kind_for_ext(ext),
        "msg_id": message.message_id,
        "file_id": file_id,
        "caption": message.caption or "",
    }


@router.channel_post(F.chat.id == FILE_CHANNEL_ID)
async def on_file_post(message: Message) -> None:
    if not FILE_CHANNEL_ID:
        return
    item = _extract(message)
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
