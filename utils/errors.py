"""
utils/errors.py — Sentry-style error capture (Mongo `errors`).

The global aiogram error handler (bot.py) calls capture() on any unhandled
exception, persisting a compact record (type, message, truncated traceback,
context) so admins can see an error feed in 🩺 Health without tailing logs. A TTL
index on `at` (database.connection) auto-expires old records.
"""
import logging
import traceback
from datetime import datetime, timezone

from pymongo import DESCENDING

from database.connection import MongoManager

logger = logging.getLogger(__name__)


async def capture(exc: BaseException, where: str = "") -> None:
    """Persist one error record + bump the in-process error counter. Never raises."""
    try:
        from utils.metrics import incr
        incr("errors")
    except Exception:  # noqa: BLE001
        pass
    try:
        db = await MongoManager.get()
        await db.safe_insert("errors", {
            "where": (where or "")[:200],
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "trace": "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))[-2000:],
            "at": datetime.now(timezone.utc),
        })
    except Exception:  # noqa: BLE001 — capturing an error must never raise
        logger.debug("error capture failed", exc_info=True)


async def recent(limit: int = 15) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("errors", {}, limit=limit, sort=[("at", DESCENDING)])


async def count() -> int:
    db = await MongoManager.get()
    return await db.count_global("errors")
