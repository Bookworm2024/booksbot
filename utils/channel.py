"""
utils/channel.py — the live, admin-editable file/database channel id.

The channel that holds the book/audiobook archive used to be a frozen env
constant (config.FILE_CHANNEL_ID), baked into the indexer's channel_post filter
at import time. That made it impossible to change without a redeploy — and if it
was unset, NOTHING got indexed.

Now the id lives in Mongo `kv` under "file_channel_id" and is read at runtime, so
the super-admin can repoint the bot at a new channel from the admin panel (just
send the chat id) with zero downtime. config.FILE_CHANNEL_ID remains only as the
first-run seed/default so existing env-configured deploys keep working.
"""
from database.connection import MongoManager

# Tiny in-process cache: the channel id is read on every channel_post and every
# file delivery, but changes almost never — so we cache and bust on write.
_cached: int | None = None
_loaded: bool = False


async def get_file_channel() -> int:
    """The live file-channel chat id (0 if none configured)."""
    global _cached, _loaded
    if _loaded:
        return _cached or 0
    db = await MongoManager.get()
    raw = await db.kv_get("file_channel_id", None)
    if raw in (None, ""):
        from config import FILE_CHANNEL_ID  # seed/default
        _cached = int(FILE_CHANNEL_ID or 0)
    else:
        try:
            _cached = int(raw)
        except (TypeError, ValueError):
            _cached = 0
    _loaded = True
    return _cached or 0


async def set_file_channel(chat_id: int) -> None:
    """Repoint the bot at a new file channel and bust the cache."""
    global _cached, _loaded
    db = await MongoManager.get()
    await db.kv_set("file_channel_id", int(chat_id))
    _cached = int(chat_id)
    _loaded = True
