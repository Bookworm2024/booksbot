"""
utils/battlepass.py — seasonal Battle Pass (Growth).

Each calendar month is a season. Core actions (download/game/spin/claim) earn
Pass Points (pp), accrued centrally from utils.missions.mark via bump(). Reaching
a tier's pp threshold unlocks a free reward; buying the premium pass (BGM) for the
season unlocks the bigger premium reward at every tier too.

State on the user doc, all season-scoped:
  bp_season "YYYY-MM" · bp_pp int · bp_claimed [tier indices]
  bp_premium_season "YYYY-MM" (the season the premium pass was bought for)
"""
import logging
from datetime import datetime, timezone

from database.connection import MongoManager
from utils.wallet import add_bgm, charge_bgm

logger = logging.getLogger(__name__)

PREMIUM_PRICE = 20.0  # BGM to unlock the premium track for the season

# pp earned per core action
PP_PER = {"download": 8, "play_game": 5, "spin": 3, "claim": 5}

# (pp threshold, free reward BGM, premium reward BGM)
TIERS = [
    (50,   0.2, 1.0),
    (120,  0.3, 1.5),
    (220,  0.5, 2.0),
    (350,  0.7, 3.0),
    (500,  1.0, 4.0),
    (700,  1.5, 6.0),
    (1000, 2.5, 10.0),
]


def season() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def bump(uid: int, key: str) -> None:
    """Add Pass Points for an action; resets the counter atomically on a new
    season (guarded so a concurrent action can't clobber it). Never raises."""
    pp = PP_PER.get(key, 0)
    if pp <= 0:
        return
    try:
        db = await MongoManager.get()
        s = season()
        did_reset = await db.find_one_and_update_global(
            "users", {"user_id": uid, "bp_season": {"$ne": s}},
            {"$set": {"bp_season": s, "bp_pp": pp, "bp_claimed": []}})
        if not did_reset:
            await db.safe_update("users", {"user_id": uid}, {"$inc": {"bp_pp": pp}})
    except Exception:  # noqa: BLE001
        logger.debug("battlepass.bump failed for %s/%s", uid, key, exc_info=True)


async def status(uid: int) -> dict:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}) or {}
    s = season()
    fresh = doc.get("bp_season") == s
    pp = int(doc.get("bp_pp") or 0) if fresh else 0
    claimed = set(doc.get("bp_claimed") or []) if fresh else set()
    premium = doc.get("bp_premium_season") == s
    tiers = []
    for i, (thr, free, prem) in enumerate(TIERS):
        tiers.append({
            "idx": i, "threshold": thr, "free": free, "premium": prem,
            "reached": pp >= thr, "claimed": i in claimed,
            "claimable": pp >= thr and i not in claimed,
        })
    return {"pp": pp, "premium": premium, "season": s, "tiers": tiers,
            "max_pp": TIERS[-1][0]}


async def claim(uid: int, idx: int) -> float:
    """Claim one reached tier exactly once. Pays the free reward plus the premium
    reward if the premium pass is active. Returns BGM paid (0 if not eligible)."""
    if not (0 <= idx < len(TIERS)):
        return 0.0
    thr, free, prem = TIERS[idx]
    db = await MongoManager.get()
    s = season()
    updated = await db.find_one_and_update_global(
        "users",
        {"user_id": uid, "bp_season": s, "bp_pp": {"$gte": thr},
         "bp_claimed": {"$ne": idx}},
        {"$addToSet": {"bp_claimed": idx}})
    if not updated:
        return 0.0
    premium = (updated.get("bp_premium_season") == s)
    reward = round(free + (prem if premium else 0.0), 3)
    await add_bgm(uid, reward)
    return reward


async def buy_premium(uid: int) -> str:
    """Unlock the premium track for the current season. Returns 'ok',
    'already', or 'insufficient'.

    Charge FIRST, then claim the season slot atomically. This avoids exposing a
    transient 'claimed-but-unpaid' state to a concurrent reader (which would see
    'already' for a slot that then gets reverted). A rare double-tap where the
    second charge succeeds but the slot is already claimed is refunded, so the
    user is never double-charged."""
    db = await MongoManager.get()
    s = season()
    if not await charge_bgm(uid, PREMIUM_PRICE):
        return "insufficient"
    claimed = await db.find_one_and_update_global(
        "users", {"user_id": uid, "bp_premium_season": {"$ne": s}},
        {"$set": {"bp_premium_season": s}})
    if not claimed:
        # already premium this season (e.g. a double-tap) → refund the extra charge
        await add_bgm(uid, PREMIUM_PRICE)
        return "already"
    return "ok"
