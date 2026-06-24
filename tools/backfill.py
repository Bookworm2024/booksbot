"""
tools/backfill.py — index the file channel's full history into Mongo.

Why a userbot: the Telegram Bot API only delivers NEW channel posts, never
history. Telethon (a user session) can iterate every past message, so this is
how the ~30k existing files become searchable.

Run after generate_session.py:

    python tools/backfill.py

It walks FILE_CHANNEL_ID oldest→newest, extracting a clean title + the file_id
for every document/audio/video, and upserts into the `files` collection. Safe
to re-run: dedupe is on file_unique_id, and a resume cursor (last indexed
message id) is stored in `kv` so an interrupted run continues where it stopped.

NOTE: file_id values captured via a USER session are not directly reusable by
the BOT to send files. The bot must obtain its own file_id by being able to
access the message. Strategy (implemented in a later phase): store the channel
message_id and have the bot copy/forward the message to the user via
bot.copy_message(FILE_CHANNEL_ID, msg_id). This module therefore stores BOTH
the user-session file_id (for metadata) and the channel msg_id (for delivery).
"""
import asyncio
import logging
import re

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_HASH, API_ID, FILE_CHANNEL_ID, TELETHON_SESSION
from database.connection import MongoManager
from utils.files import index_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill")

_TAG_RE = re.compile(r"@\w+")


def clean_name(raw: str) -> str:
    """Strip @mentions and turn separators into spaces for readable titles."""
    name = _TAG_RE.sub("", raw or "")
    name = name.replace("_", " ").replace("-", " ").replace(".", " ")
    return " ".join(name.split()).strip()


def extract(msg) -> dict | None:
    """Pull (name, file_unique_id, file_id, kind, ext) from a Telethon message."""
    doc = getattr(msg, "document", None)
    if not doc:
        return None
    raw_name = ""
    for attr in doc.attributes:
        if getattr(attr, "file_name", None):
            raw_name = attr.file_name
            break
    if not raw_name:
        raw_name = (msg.message or "").split("\n")[0]
    if not raw_name:
        return None
    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else ""
    kind = "audio" if ext in ("mp3", "m4b", "m4a", "wav", "ogg", "flac") else "document"
    name = clean_name(raw_name)
    return {
        "name": name,
        "name_lc": name.lower(),
        "ext": ext,
        "kind": kind,
        "file_unique_id": str(doc.id),
        "msg_id": msg.id,
    }


async def run() -> None:
    if not (API_ID and API_HASH and TELETHON_SESSION):
        raise SystemExit("Set API_ID, API_HASH and TELETHON_SESSION first.")

    db = await MongoManager.get()
    # Prefer the live channel set in-bot (admin panel → 🗂 File Channel); fall back
    # to the FILE_CHANNEL_ID env constant. So in-bot config and backfill agree.
    channel = int(await db.kv_get("file_channel_id", 0) or 0) or FILE_CHANNEL_ID
    if not channel:
        raise SystemExit("No file channel set. Set it in-bot (🗂 File Channel) or via FILE_CHANNEL_ID.")
    resume_id = int(await db.kv_get("backfill_last_msg_id", 0) or 0)
    logger.info("Backfilling channel %d, resuming from msg_id > %d", channel, resume_id)

    client = TelegramClient(StringSession(TELETHON_SESSION), API_ID, API_HASH)
    await client.start()

    indexed = 0
    async for msg in client.iter_messages(channel, reverse=True, min_id=resume_id):
        item = extract(msg)
        if item:
            # stamp the source channel so delivery survives a later channel change
            # and so (chan_id, msg_id) dedupes against Bot-API-indexed copies.
            item["chan_id"] = channel
            # index_file stamps indexed_at + the trigram index (for fuzzy search)
            created = await index_file(item)
            if created:
                indexed += 1
        if msg.id > resume_id:
            resume_id = msg.id
            if indexed and indexed % 500 == 0:
                await db.kv_set("backfill_last_msg_id", resume_id)
                logger.info("Indexed %d new files (cursor=%d)…", indexed, resume_id)

    await db.kv_set("backfill_last_msg_id", resume_id)
    total = await db.count_global("files")
    logger.info("Done. +%d new this run, %d total in index.", indexed, total)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
