"""
utils/challenges.py — monthly reading challenges.

Longer-horizon goals than daily missions: count actions across the calendar
month and pay a BGM bonus when a target is met. Counters live on the user doc
and are maintained from one place — utils.missions.mark calls bump() for the
four core actions (download / play_game / spin / claim). Everything resets when
the month rolls over.

  chal_month    "YYYY-MM" the counters belong to
  chal_downloads / chal_games / chal_spins / chal_claims   int counters
  chal_claimed  [challenge keys already paid this month]
"""
import logging
from datetime import datetime, timezone

from database.connection import MongoManager
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)

# key → (emoji, title, description, counter field, target, reward BGM)
CHALLENGES = [
    ("read5",   "📚", "Bookworm",      "Add 5 books to your shelf this month",   "chal_downloads", 5,  1.0),
    ("read20",  "📖", "Devourer",      "Collect 20 books this month",            "chal_downloads", 20, 3.0),
    ("game10",  "🎮", "Game On",       "Play 10 games this month",               "chal_games",     10, 1.0),
    ("spin10",  "🎡", "Lucky Spinner", "Take 10 spins of the wheel",             "chal_spins",     10, 1.0),
    ("claim15", "🪙", "Daily Devotee", "Claim your daily BCN on 15 days",        "chal_claims",    15, 1.5),
]

_FIELDS = {"download": "chal_downloads", "play_game": "chal_games",
           "spin": "chal_spins", "claim": "chal_claims"}
_ALL_FIELDS = ["chal_downloads", "chal_games", "chal_spins", "chal_claims"]


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def bump(uid: int, key: str) -> None:
    """Increment the monthly counter for an action, resetting on a month change.
    Safe to call from anywhere; never raises into the host action."""
    field = _FIELDS.get(key)
    if not field:
        return
    try:
        db = await MongoManager.get()
        mkey = _month()
        # Atomic, self-guarding reset: only the FIRST action in a new month matches
        # {chal_month != mkey} and resets — and it sets its own field to 1 in the
        # SAME op so its increment isn't lost. A concurrent second action no longer
        # matches the reset filter and falls through to a plain $inc, so a
        # month-rollover race can never clobber a counter.
        reset = {f: 0 for f in _ALL_FIELDS}
        reset[field] = 1
        reset["chal_month"] = mkey
        reset["chal_claimed"] = []
        did_reset = await db.find_one_and_update_global(
            "users", {"user_id": uid, "chal_month": {"$ne": mkey}}, {"$set": reset})
        if not did_reset:
            await db.safe_update("users", {"user_id": uid}, {"$inc": {field: 1}})
    except Exception:  # noqa: BLE001
        logger.debug("challenges.bump failed for %s/%s", uid, key, exc_info=True)


async def status(uid: int) -> list[dict]:
    """Per-challenge progress for the current month."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}) or {}
    fresh = doc.get("chal_month") == _month()
    claimed = set(doc.get("chal_claimed") or []) if fresh else set()
    out = []
    for ckey, emoji, title, desc, field, target, reward in CHALLENGES:
        have = int(doc.get(field) or 0) if fresh else 0
        done = have >= target
        out.append({
            "key": ckey, "emoji": emoji, "title": title, "desc": desc,
            "have": min(have, target), "target": target, "reward": reward,
            "done": done, "claimed": ckey in claimed,
            "claimable": done and ckey not in claimed,
        })
    return out


async def claim(uid: int, ckey: str) -> float:
    """Pay out one completed challenge, exactly once per month. Returns the BGM
    paid (0 if not eligible). Atomic on chal_claimed via a $nin-guarded update."""
    spec = next((c for c in CHALLENGES if c[0] == ckey), None)
    if not spec:
        return 0.0
    _k, _e, _t, _d, field, target, reward = spec
    db = await MongoManager.get()
    mkey = _month()
    # Atomic: only succeeds if this month's counter met the target AND the key
    # hasn't been claimed yet — guards against a double-tap paying twice.
    updated = await db.find_one_and_update_global(
        "users",
        {"user_id": uid, "chal_month": mkey, field: {"$gte": target},
         "chal_claimed": {"$ne": ckey}},
        {"$addToSet": {"chal_claimed": ckey}})
    if not updated:
        return 0.0
    await add_bgm(uid, reward)
    return reward
