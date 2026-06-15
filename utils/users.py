"""
utils/users.py — user records & access helpers, backed by Mongo `users`.

Schema (collection `users`):
  user_id     int    (unique)
  first_name  str
  username    str
  is_banned   bool
  joined_at   datetime
  last_active datetime
  bookgem     float   (BGM — permanent currency)
  bookcoin    float   (BCN — daily, expiring)
  bcn_claimed_at datetime|None
  referrer    int|None
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from database.connection import MongoManager

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def backfill_first_purchase_flag() -> None:
    """One-time migration: mark users who ALREADY have a paid order as past their
    first purchase, so the first-purchase bonus never retroactively pays existing
    buyers. Genuine first-time buyers (incl. imported users who never purchased)
    keep the flag absent and still earn the bonus on their real first purchase.
    Guarded by a kv flag so it runs exactly once."""
    db = await MongoManager.get()
    if await db.kv_get("first_purchase_migrated", False):
        return
    buyer_ids = set()
    for coll in ("payments", "crypto_orders"):
        for r in await db.find_global(coll, {"status": "paid"}, proj={"user_id": 1}):
            if r.get("user_id"):
                buyer_ids.add(r["user_id"])
    for uid in buyer_ids:
        await db.safe_update("users", {"user_id": uid},
                             {"$set": {"first_purchase_done": True}}, upsert=False)
    await db.kv_set("first_purchase_migrated", True)
    logger.info("first-purchase backfill: flagged %d existing buyer(s).", len(buyer_ids))


async def ensure_user(user_id: int, first_name: str = "", username: str = "") -> Dict[str, Any]:
    """Fetch the user; create the record on first sight. Returns the doc."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": user_id})
    if doc:
        # keep display fields fresh + bump activity
        await db.safe_update(
            "users", {"user_id": user_id},
            {"$set": {"first_name": first_name or doc.get("first_name", ""),
                      "username": username or doc.get("username", ""),
                      "last_active": _now()}},
        )
        return doc

    new = {
        "user_id": user_id,
        "first_name": first_name,
        "username": username,
        "is_banned": False,
        "joined_at": _now(),
        "last_active": _now(),
        "bookgem": 0.0,
        "bookcoin": 0.0,
        "bcn_claimed_at": None,
        "referrer": None,
        "is_new": True,
    }
    await db.safe_insert("users", new)
    return new


async def is_banned(user_id: int) -> bool:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": user_id}, {"is_banned": 1})
    return bool(doc and doc.get("is_banned"))


async def set_ban(user_id: int, banned: bool) -> None:
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": user_id}, {"$set": {"is_banned": banned}})


async def get_balance(user_id: int) -> tuple[float, float]:
    """Return (bookgem, bookcoin)."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": user_id}, {"bookgem": 1, "bookcoin": 1})
    if not doc:
        return 0.0, 0.0
    return float(doc.get("bookgem") or 0), float(doc.get("bookcoin") or 0)
