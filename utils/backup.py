"""
utils/backup.py — automated config/economy backups.

Periodically exports the small but high-value collections (settings, redeem
codes, coupons, question bank) plus a stats summary to a JSON document and posts
it to the backup channel (kv `backup_channel`, else LOG_CHANNEL_ID). The bulk
collections (users / files) are best backed up by the database provider (Atlas
snapshots); this protects the in-bot configuration & economy state that lives
only in Mongo `kv`.

Runs on a loop (BACKUP_INTERVAL_HOURS, default 24) and on demand from 🩺 Health.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from aiogram.types import BufferedInputFile

from config import LOG_CHANNEL_ID
from database.connection import MongoManager

logger = logging.getLogger(__name__)

# small, config/economy-critical collections worth a full export
_COLLECTIONS = ["kv", "codes", "coupons", "questions"]


def _now():
    return datetime.now(timezone.utc)


async def backup_channel() -> int:
    db = await MongoManager.get()
    ch = await db.kv_get("backup_channel", 0)
    try:
        return int(ch) or LOG_CHANNEL_ID
    except (TypeError, ValueError):
        return LOG_CHANNEL_ID


async def build_backup() -> tuple[bytes, dict]:
    """Return (json_bytes, summary). Deduped across clusters by a stable key."""
    db = await MongoManager.get()
    data: dict = {"generated_at": _now().isoformat(), "collections": {}}
    summary: dict = {}
    for coll in _COLLECTIONS:
        seen, rows = set(), []
        for idx in db.healthy:
            async for d in db.dbs[idx][coll].find({}):
                d.pop("_id", None)
                key = json.dumps(d, default=str, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(d)
        data["collections"][coll] = rows
        summary[coll] = len(rows)
    data["counts"] = {
        "users": await db.count_global("users"),
        "files": await db.count_global("files"),
        "requests": await db.count_global("requests"),
    }
    summary["users"] = data["counts"]["users"]
    summary["files"] = data["counts"]["files"]
    payload = json.dumps(data, default=str, ensure_ascii=False, indent=2).encode("utf-8")
    return payload, summary


async def backup_now(bot) -> dict:
    """Build + send a backup immediately. Returns the summary (raises on send)."""
    payload, summary = await build_backup()
    ch = await backup_channel()
    fname = f"booksbot_backup_{_now().strftime('%Y%m%d_%H%M')}.json"
    caption = ("🗄 <b>Automated Backup</b>\n"
               + " · ".join(f"{k}: {v}" for k, v in summary.items()))
    if ch:
        await bot.send_document(ch, BufferedInputFile(payload, filename=fname), caption=caption)
    return summary


async def run_backup_loop(bot) -> None:
    """Background worker: post a backup every BACKUP_INTERVAL_HOURS (default 24).
    No-ops quietly when no backup channel is configured."""
    import os
    try:
        hours = float(os.getenv("BACKUP_INTERVAL_HOURS", "24") or 24)
    except (TypeError, ValueError):
        hours = 24.0
    interval = max(3600, int(hours * 3600))
    # small initial delay so startup work settles first
    await asyncio.sleep(120)
    while True:
        try:
            if await backup_channel():
                summary = await backup_now(bot)
                logger.info("backup posted: %s", summary)
            else:
                logger.debug("backup loop: no backup channel set, skipping")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a backup failure must not kill the worker
            logger.exception("backup loop iteration failed")
        await asyncio.sleep(interval)
