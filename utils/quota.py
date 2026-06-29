"""
utils/quota.py — per-user 24h feature quotas (the freemium gate).

Replaces the BGM/BCN cost that used to ration downloads, requests and AI. Each
quota is a UTC-day rolling counter on the user doc:
    q_<key>_d  str   the UTC date ("YYYY-MM-DD") the counter belongs to
    q_<key>_n  int   uses so far today

Limits come from live settings (admin-tunable, no redeploy) and depend on the
user's tier:  0 = feature closed · -1 = unlimited · n = n per 24h.

Race-safety mirrors handlers/memory.py's proven faucet: the new-day reset writes
``n = 1`` in the SAME atomic op as the day flip, and the same-day path is a
conditional ``$inc`` under the cap — so two concurrent calls can never both slip
past the limit. All ops go through find_one_and_update_global, which finds the
user's doc on whichever cluster holds it.

`key` (the counter field) and `kind` (which limit pair to read) are separate so
many per-game counters (game_quiz, game_hangman, …) can share one limit ("game").
"""
from datetime import datetime, timezone
from typing import Optional

from database.connection import MongoManager
from utils.settings import get_float

# kind → (free settings key, premium settings key)
_LIMIT_KEYS: dict[str, tuple[str, str]] = {
    "dl":         ("q_dl_free", "q_dl_premium"),
    "mreq":       ("q_mreq_free", "q_mreq_premium"),
    "mreq_audio": ("q_mreq_audio_free", "q_mreq_audio_premium"),
    "airec":      ("q_airec_free", "q_airec_premium"),
    "aisum":      ("q_aisum_free", "q_aisum_premium"),
    "game":       ("q_game_free", "q_game_premium"),
}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def limit(uid: int, kind: str) -> Optional[int]:
    """Resolved 24h limit for this user & feature: None = unlimited, 0 = closed,
    else the integer cap. Reads live settings and the user's premium tier."""
    free_k, prem_k = _LIMIT_KEYS[kind]
    from utils.premium import is_premium
    prem = await is_premium(uid)
    raw = int(await get_float(prem_k if prem else free_k))
    return None if raw < 0 else max(0, raw)


async def used(uid: int, key: str) -> int:
    """Uses so far in the current UTC day for this counter (0 if a new day)."""
    db = await MongoManager.get()
    df, nf = f"q_{key}_d", f"q_{key}_n"
    doc = await db.find_one_global("users", {"user_id": uid}, {df: 1, nf: 1})
    if not doc or doc.get(df) != _today():
        return 0
    return int(doc.get(nf) or 0)


async def status(uid: int, key: str, kind: Optional[str] = None) -> tuple[int, Optional[int]]:
    """(used_today, limit) — limit None = unlimited, 0 = closed. For display."""
    return await used(uid, key), await limit(uid, kind or key)


async def can(uid: int, key: str, kind: Optional[str] = None) -> bool:
    """True if the user may use this feature now (without consuming)."""
    lim = await limit(uid, kind or key)
    if lim is None:
        return True
    if lim <= 0:
        return False
    return await used(uid, key) < lim


async def consume(uid: int, key: str, kind: Optional[str] = None) -> bool:
    """Atomically count one use under the cap. Returns False when the feature is
    closed (limit 0) or the cap is reached; True (and increments) otherwise.
    Unlimited tiers always return True and still record usage for stats."""
    lim = await limit(uid, kind or key)
    today = _today()
    df, nf = f"q_{key}_d", f"q_{key}_n"
    db = await MongoManager.get()

    if lim == 0:
        return False

    # New UTC day → reset to 1 in the same atomic op as the day flip. A missing
    # field also matches ($ne today), so the very first use initialises cleanly.
    reset = await db.find_one_and_update_global(
        "users", {"user_id": uid, df: {"$ne": today}},
        {"$set": {df: today, nf: 1}})
    if reset is not None:
        return True

    if lim is None:
        # unlimited: just bump the same-day counter (best-effort, for stats)
        await db.find_one_and_update_global(
            "users", {"user_id": uid, df: today}, {"$inc": {nf: 1}})
        return True

    # same day → conditional increment under the cap (race-safe)
    inc = await db.find_one_and_update_global(
        "users", {"user_id": uid, df: today, nf: {"$lt": lim}},
        {"$inc": {nf: 1}})
    return inc is not None


async def refund_one(uid: int, key: str) -> None:
    """Give back one use (e.g. a delivery failed after the quota was consumed),
    only within today's window and never below zero."""
    db = await MongoManager.get()
    df, nf = f"q_{key}_d", f"q_{key}_n"
    await db.find_one_and_update_global(
        "users", {"user_id": uid, df: _today(), nf: {"$gt": 0}},
        {"$inc": {nf: -1}})


def fmt_limit(lim: Optional[int]) -> str:
    """Human label for a resolved limit (∞ / closed / the number)."""
    if lim is None:
        return "∞"
    if lim <= 0:
        return "—"
    return str(lim)
