"""
utils/coupons.py — promo coupons that add a BONUS to a BGM purchase.

A coupon is applied during the Buy flow and redeemed at payment confirmation:
  • kind "pct"  → bonus = base BGM × value%
  • kind "flat" → bonus = value BGM
Redemption is ATOMIC and bounded:
  • one redemption per user  → unique index on coupon_uses(code, user_id)
  • a global cap (max_uses)   → atomic uses<max_uses increment on the coupon
  • not expired / still active
Validate() gives best-effort feedback at apply time; redeem() is the source of
truth at credit time (so an unpaid 'apply' never consumes anything).
"""
import logging
import random
import string
from datetime import datetime, timedelta, timezone

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from database.connection import MongoManager

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def _code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def compute_bonus(coupon: dict, base: float) -> float:
    if coupon.get("kind") == "flat":
        return round(float(coupon.get("value") or 0), 3)
    return round(base * float(coupon.get("value") or 0) / 100.0, 3)


async def create_coupon(kind: str, value: float, max_uses: int, days: int, by: int) -> str:
    db = await MongoManager.get()
    code = _code()
    await db.safe_insert("coupons", {
        "code": code, "kind": kind, "value": float(value),
        "max_uses": int(max_uses), "uses": 0, "active": True,
        "expires_at": _now() + timedelta(days=int(days)),
        "created_by": by, "created_at": _now()})
    return code


async def get_coupon(code: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("coupons", {"code": code.upper()})


async def validate(code: str, uid: int) -> tuple[bool, object]:
    """Best-effort check at apply time. Returns (True, coupon) or (False, reason)."""
    code = (code or "").strip().upper()
    db = await MongoManager.get()
    c = await db.find_one_global("coupons", {"code": code})
    if not c or not c.get("active"):
        return False, "unknown"
    if c.get("expires_at") and c["expires_at"] <= _now():
        return False, "expired"
    if int(c.get("uses") or 0) >= int(c.get("max_uses") or 0):
        return False, "exhausted"
    if await db.find_one_global("coupon_uses", {"code": code, "user_id": uid}):
        return False, "used"
    return True, c


async def redeem(code: str, uid: int, base: float) -> float:
    """Atomically redeem at credit time. Returns the bonus BGM (0 if not allowed).
    Reserves the per-user slot first, then the global cap; rolls back the per-user
    record if the global cap is already hit."""
    code = (code or "").strip().upper()
    if not code:
        return 0.0
    db = await MongoManager.get()
    # 1) claim the per-user slot. Check ALL clusters first (the per-cluster unique
    # index can't catch a prior use that lives on a different cluster after a
    # write-failover), then insert — same cluster-safe pattern as wallet.spend.
    if await db.find_one_global("coupon_uses", {"code": code, "user_id": uid}):
        return 0.0
    try:
        ok = await db.safe_insert("coupon_uses",
                                  {"code": code, "user_id": uid, "at": _now()})
        if not ok:   # safe_insert returns False on DuplicateKeyError
            return 0.0
    except DuplicateKeyError:
        return 0.0
    # 2) reserve a global use atomically (active, not expired, under cap)
    reserved = await db.find_one_and_update_global(
        "coupons",
        {"code": code, "active": True, "expires_at": {"$gt": _now()},
         "$expr": {"$lt": ["$uses", "$max_uses"]}},
        {"$inc": {"uses": 1}})
    if not reserved:
        # cap hit / expired after the user slot was claimed → roll the slot back
        for idx in db.healthy:
            await db.dbs[idx]["coupon_uses"].delete_one({"code": code, "user_id": uid})
        return 0.0
    return compute_bonus(reserved, base)


async def active_coupons(limit: int = 15) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("coupons", {"active": True}, limit=limit,
                                sort=[("created_at", DESCENDING)])
