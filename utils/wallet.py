"""
utils/wallet.py — the token economy.

Two currencies (mirroring the TBC bot):
  • BGM  "bookgem"   — permanent, never expires. Bought, won, redeemed, refunded.
  • BCN  "bookcoin"  — free daily claim, EXPIRES after BCN_EXPIRY_SECONDS (24h).

Spend order is BCN-first (use the expiring currency before the permanent one),
matching the original bot. spend() reports which currency was used so a failed
delivery can be refunded to the right bucket.
"""
from datetime import datetime, timezone
from typing import Optional

from config import BCN_EXPIRY_SECONDS
from database.connection import MongoManager


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def check_bcn_expiry(user_id: int) -> None:
    """Zero out expired BCN. Call before any balance read/spend."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": user_id},
                                   {"bookcoin": 1, "bcn_claimed_at": 1})
    if not doc:
        return
    claimed = doc.get("bcn_claimed_at")
    bcn = float(doc.get("bookcoin") or 0)
    if bcn > 0 and claimed:
        age = (_now() - claimed).total_seconds()
        if age > BCN_EXPIRY_SECONDS:
            await db.safe_update("users", {"user_id": user_id},
                                 {"$set": {"bookcoin": 0.0, "bcn_claimed_at": None}})


async def get_balances(user_id: int) -> tuple[float, float]:
    """Return (bgm, bcn) after applying expiry."""
    await check_bcn_expiry(user_id)
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": user_id},
                                   {"bookgem": 1, "bookcoin": 1})
    if not doc:
        return 0.0, 0.0
    return float(doc.get("bookgem") or 0), float(doc.get("bookcoin") or 0)


async def add_bgm(user_id: int, amount: float) -> None:
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": user_id}, {"$inc": {"bookgem": float(amount)}})


async def cut_bgm(user_id: int, amount: float) -> None:
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": user_id}, {"$inc": {"bookgem": -float(amount)}})


async def set_daily_bcn(user_id: int, amount: float) -> None:
    """Set the daily claim (replaces any leftover) and stamp claim time."""
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": user_id},
                         {"$set": {"bookcoin": float(amount), "bcn_claimed_at": _now()}})


async def seconds_until_claim(user_id: int) -> int:
    """0 == claimable now; else seconds remaining on the cooldown."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": user_id}, {"bcn_claimed_at": 1})
    claimed = doc.get("bcn_claimed_at") if doc else None
    if not claimed:
        return 0
    age = (_now() - claimed).total_seconds()
    return max(0, int(BCN_EXPIRY_SECONDS - age))


async def spend(user_id: int, cost: float) -> Optional[str]:
    """Deduct `cost`, BCN-first then BGM. Returns 'BCN'/'BGM' on success, or
    None if the user can't afford it. Atomic-ish via conditional updates."""
    await check_bcn_expiry(user_id)
    db = await MongoManager.get()
    cost = float(cost)
    # Try BCN first (only where it lives — find the owning cluster).
    for idx in db.healthy:
        coll = db.dbs[idx]["users"]
        # BCN path
        res = await coll.update_one(
            {"user_id": user_id, "bookcoin": {"$gte": cost}},
            {"$inc": {"bookcoin": -cost}},
        )
        if res.modified_count:
            return "BCN"
    for idx in db.healthy:
        coll = db.dbs[idx]["users"]
        res = await coll.update_one(
            {"user_id": user_id, "bookgem": {"$gte": cost}},
            {"$inc": {"bookgem": -cost}},
        )
        if res.modified_count:
            return "BGM"
    return None


async def refund(user_id: int, amount: float, currency: str) -> None:
    db = await MongoManager.get()
    field = "bookcoin" if currency == "BCN" else "bookgem"
    await db.safe_update("users", {"user_id": user_id}, {"$inc": {field: float(amount)}})
