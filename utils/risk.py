"""
utils/risk.py — fraud flags, velocity checks & anti-multiaccount signals.

Tracks the rate of abuse-prone actions (gifting, rapid referrals) per UTC day and
auto-flags a user who exceeds the limit, so the abuse vector for multi-accounting
(farm signups → funnel BGM to one account via gifts) gets surfaced for admin
review. Flagged users are blocked from gifting. All flags are reversible.

User-doc fields: risk_flag (bool), risk_reason, risk_at; risk_day + per-action
counters (risk_gift / risk_referral / risk_convert / risk_redeem).
"""
import logging
from datetime import datetime, timezone

from database.connection import MongoManager

logger = logging.getLogger(__name__)

# per-UTC-day count that auto-flags the account for review
VELOCITY_LIMITS = {"gift": 8, "referral": 18, "convert": 6, "redeem": 12}
_FIELDS = ["risk_gift", "risk_referral", "risk_convert", "risk_redeem"]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now():
    return datetime.now(timezone.utc)


async def is_flagged(uid: int) -> bool:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"risk_flag": 1})
    return bool(doc and doc.get("risk_flag"))


async def flag_user(uid: int, reason: str) -> None:
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"risk_flag": True, "risk_reason": reason[:120],
                                   "risk_at": _now()}})
    logger.warning("risk: flagged %s — %s", uid, reason)


async def unflag_user(uid: int) -> None:
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"risk_flag": False},
                          "$unset": {"risk_reason": "", "risk_at": ""}}, upsert=False)


async def flagged_users(limit: int = 20) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("users", {"risk_flag": True}, limit=limit,
                                proj={"user_id": 1, "first_name": 1, "risk_reason": 1,
                                      "risk_at": 1})


async def record(uid: int, action: str) -> int:
    """Increment today's counter for `action` (resetting on a new day, atomically
    so a concurrent action can't clobber it) and auto-flag if over the limit.
    Returns today's count. Best-effort — never raises into the host action."""
    field = f"risk_{action}"
    if field not in _FIELDS:
        return 0
    try:
        db = await MongoManager.get()
        day = _today()
        reset = {f: 0 for f in _FIELDS}
        reset[field] = 1
        reset["risk_day"] = day
        did_reset = await db.find_one_and_update_global(
            "users", {"user_id": uid, "risk_day": {"$ne": day}}, {"$set": reset})
        if did_reset:
            count = 1
        else:
            after = await db.find_one_and_update_global(
                "users", {"user_id": uid}, {"$inc": {field: 1}})
            count = int((after or {}).get(field) or 0)
        limit = VELOCITY_LIMITS.get(action, 10**9)
        if count > limit and not await is_flagged(uid):
            await flag_user(uid, f"velocity: {action} {count}/day (limit {limit})")
        return count
    except Exception:  # noqa: BLE001
        logger.debug("risk.record failed for %s/%s", uid, action, exc_info=True)
        return 0
