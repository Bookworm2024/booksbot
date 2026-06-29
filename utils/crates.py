"""
utils/crates.py — loot crates (growth / reward loop).

Every few core actions (download / game / spin / claim) earns a crate key,
accrued centrally from utils.missions.mark via bump(). Opening a crate rolls a
weighted reward — small wins common, a rare jackpot. Keys + progress live on the
user doc; opening atomically consumes one key so it can't be double-spent.
"""
import logging
import random
from datetime import datetime, timezone

from database.connection import MongoManager
from utils.format import sanitize_amount

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)

ACTIONS_PER_KEY = 5

# (tier label, BGM, BCN, weight) — common small, legendary rare.
# BCN is retired in the freemium model, so its old value is folded into BGM and
# the BCN column stays 0 (kept only so the return shape is unchanged).
_TIERS = [
    ("⚪ Common", 0.1, 0, 38),
    ("⚪ Common", 0.2, 0, 25),
    ("🟢 Uncommon", 0.5, 0, 18),
    ("🔵 Rare", 2.0, 0, 10),
    ("🟣 Epic", 4.0, 0, 6),
    ("🟡 Legendary", 5.0, 0, 3),
]
# Rarity ladder shown to the reader: Common → Uncommon → Rare → Epic → Legendary.
_BAG = [t for t in _TIERS for _ in range(t[3])]


async def bump(uid: int) -> None:
    """Add one action's worth of crate progress, converting full progress into
    keys atomically. Safe to call from anywhere; never raises into the host."""
    try:
        db = await MongoManager.get()
        await db.safe_update("users", {"user_id": uid}, {"$inc": {"crate_progress": 1}})
        # convert each full ACTIONS_PER_KEY chunk into a key (atomic, race-safe)
        for _ in range(4):  # bounded loop; one action can't earn many keys
            conv = await db.find_one_and_update_global(
                "users", {"user_id": uid, "crate_progress": {"$gte": ACTIONS_PER_KEY}},
                {"$inc": {"crate_progress": -ACTIONS_PER_KEY, "crate_keys": 1}})
            if not conv:
                break
    except Exception:  # noqa: BLE001
        logger.debug("crates.bump failed for %s", uid, exc_info=True)


async def status(uid: int) -> dict:
    db = await MongoManager.get()
    doc = await db.find_one_global(
        "users", {"user_id": uid},
        {"crate_keys": 1, "crate_progress": 1, "crates_opened": 1}) or {}
    return {
        "keys": int(doc.get("crate_keys") or 0),
        "progress": int(doc.get("crate_progress") or 0) % ACTIONS_PER_KEY,
        "need": ACTIONS_PER_KEY,
        "opened": int(doc.get("crates_opened") or 0),
    }


async def open_crate(uid: int) -> dict | None:
    """Consume one key and roll a reward. Returns {tier, bgm, bcn} or None if no
    keys. The key is consumed atomically before crediting; the reward (BGM + BCN +
    opened-counter) is then credited in ONE write so consume+reward is all-or-
    nothing — if the credit fails the key is restored rather than silently lost."""
    db = await MongoManager.get()
    used = await db.find_one_and_update_global(
        "users", {"user_id": uid, "crate_keys": {"$gte": 1}},
        {"$inc": {"crate_keys": -1}})
    if not used:
        return None
    tier, bgm, bcn, _w = random.choice(_BAG)
    inc: dict = {"crates_opened": 1}
    if bgm > 0:
        inc["bookgem"] = sanitize_amount(bgm)
    if bcn > 0:
        inc["bookcoin"] = sanitize_amount(bcn)
    update: dict = {"$inc": inc}
    if bcn > 0:
        update["$set"] = {"bcn_claimed_at": _now()}  # mirror add_bcn 24h expiry
    try:
        await db.safe_update("users", {"user_id": uid}, update)
    except Exception:  # noqa: BLE001 — never consume a key without paying out
        logger.warning("crate reward credit failed for %s; restoring key", uid, exc_info=True)
        try:
            await db.find_one_and_update_global(
                "users", {"user_id": uid}, {"$inc": {"crate_keys": 1}})
        except Exception:  # noqa: BLE001
            logger.error("failed to restore crate key for %s", uid, exc_info=True)
        return None
    return {"tier": tier, "bgm": bgm, "bcn": bcn}
