"""
utils/audit.py — admin action audit trail (Mongo `audit`).

Call log_action(admin_id, action, detail) from any sensitive admin operation;
view the latest entries from the 📜 Audit Log panel.
"""
from datetime import datetime, timezone

from pymongo import DESCENDING

from database.connection import MongoManager


async def log_action(admin_id: int, action: str, detail: str = "") -> None:
    db = await MongoManager.get()
    await db.safe_insert("audit", {
        "admin_id": admin_id, "action": action, "detail": (detail or "")[:300],
        "at": datetime.now(timezone.utc),
    })


async def recent(limit: int = 20) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("audit", {}, limit=limit, sort=[("at", DESCENDING)])
