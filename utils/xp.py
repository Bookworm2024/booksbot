"""
utils/xp.py — global XP & levels.

One persistent XP pool per user (the `xp` field on the `users` doc) that every
rewarding action feeds: downloads, games, spins, daily claims, referrals. Level
is derived from XP; crossing a level boundary credits a small BGM bonus and
queues a one-time congratulations banner shown on the next dashboard visit.

Migration-safe: existing users (who pre-date the stored field) are SEEDED on
first touch from their lifetime stats, so nobody's level visibly drops to zero.
The seed mirrors the original derived formula that handlers/profile.py used, so
the number shown stays continuous across the upgrade.

Everything here is best-effort and never raises into the host action — XP is a
garnish, never a gate.
"""
import logging

from database.connection import MongoManager
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)

# action key → XP awarded. Keys for the four central actions match utils.missions
# (mark() awards XP from one place); "referral" is awarded from utils.referral.
XP_PER = {
    "download":  5,
    "play_game": 3,
    "spin":      1,
    "claim":     2,
    "referral":  20,
    "review":    4,
}

_LEVEL_STEP = 100  # XP per level

# level band → title (the highest band whose floor ≤ level wins)
_TITLES = [
    (1,  "🌱 Novice Reader"),
    (3,  "📖 Page Turner"),
    (5,  "📚 Bookworm"),
    (10, "🦉 Scholar"),
    (20, "🎓 Sage"),
    (35, "🧙 Loremaster"),
    (50, "👑 Reading Legend"),
]


def derived_xp(d: dict, favs: int) -> int:
    """The lifetime-stats baseline used to seed an existing user's XP pool (and
    historically the number profile.py displayed). Kept here as the single source
    of truth so the seed and any fallback agree."""
    return int(int(d.get("downloads") or 0) * 5
               + int(d.get("games_played") or 0) * 3
               + int(d.get("ref_count") or 0) * 20
               + len(d.get("reading_days") or []) * 2
               + int(favs) * 2)


def level_for(xp) -> int:
    try:
        xp = float(xp)
    except (TypeError, ValueError):
        xp = 0.0
    if xp < 0:
        xp = 0.0
    return 1 + int(xp) // _LEVEL_STEP


def into_level(xp) -> int:
    try:
        return int(float(xp)) % _LEVEL_STEP
    except (TypeError, ValueError):
        return 0


def title_for(level: int) -> str:
    out = _TITLES[0][1]
    for floor, name in _TITLES:
        if level >= floor:
            out = name
    return out


def level_reward(level: int) -> float:
    """BGM paid when the user reaches `level` (gently escalating, capped)."""
    return round(min(3.0, 0.1 + 0.05 * level), 2)


def progress_bar(into: int, width: int = 10) -> str:
    filled = max(0, min(width, into * width // _LEVEL_STEP))
    return "🟩" * filled + "⬜" * (width - filled)


async def _seed_if_missing(db, uid: int) -> None:
    """If the user has no stored `xp` yet, seed it from lifetime stats — exactly
    once, race-safely (the conditional update only fires while the field is
    absent), so concurrent awards can't double-seed or clobber an increment."""
    doc = await db.find_one_global(
        "users", {"user_id": uid},
        {"xp": 1, "downloads": 1, "games_played": 1, "ref_count": 1, "reading_days": 1})
    if doc is None or "xp" in doc:
        return
    favs = await db.count_global("favorites", {"user_id": uid})
    base = derived_xp(doc, favs)
    for idx in db.healthy:
        await db.dbs[idx]["users"].update_one(
            {"user_id": uid, "xp": {"$exists": False}}, {"$set": {"xp": base}})


async def award(uid: int, action: str, *, mult: float = 1.0) -> dict:
    """Award XP for `action`. Returns {gained, xp, level, leveled_up[, reward]}
    or {} if the action is unknown / no doc. On a level-up, credits a BGM bonus
    and queues the celebratory banner. Never raises."""
    try:
        gained = XP_PER.get(action, 0) * float(mult)
        if gained <= 0:
            return {}
        db = await MongoManager.get()
        await _seed_if_missing(db, uid)
        after = await db.find_one_and_update_global(
            "users", {"user_id": uid}, {"$inc": {"xp": gained}})
        if after is None:
            # no doc to atomically update — create/seed the field and stop
            await db.safe_update("users", {"user_id": uid}, {"$inc": {"xp": gained}})
            return {}
        new_xp = float(after.get("xp") or 0)
        old_xp = new_xp - gained
        old_lvl, new_lvl = level_for(old_xp), level_for(new_xp)
        info = {"gained": gained, "xp": new_xp, "level": new_lvl,
                "leveled_up": new_lvl > old_lvl}
        if new_lvl > old_lvl:
            reward = level_reward(new_lvl)
            await add_bgm(uid, reward)
            await db.safe_update("users", {"user_id": uid},
                                 {"$set": {"xp_pending_levelup": new_lvl}})
            info["reward"] = reward
        return info
    except Exception:  # noqa: BLE001 — XP must never break the host action
        logger.debug("xp.award failed for %s/%s", uid, action, exc_info=True)
        return {}


async def pop_levelup(uid: int):
    """Atomically read & clear a queued level-up. Returns the new level (int) the
    user just reached, or None. Idempotent — a second caller gets None."""
    try:
        db = await MongoManager.get()
        doc = await db.find_one_and_update_global(
            "users",
            {"user_id": uid, "xp_pending_levelup": {"$exists": True, "$ne": None}},
            {"$set": {"xp_pending_levelup": None}}, return_before=True)
        return int(doc["xp_pending_levelup"]) if doc and doc.get("xp_pending_levelup") else None
    except Exception:  # noqa: BLE001
        return None


async def levelup_banner(uid: int) -> str:
    """One-time celebratory line for the next dashboard render, or '' if none."""
    lvl = await pop_levelup(uid)
    if not lvl:
        return ""
    return (f"🎉 <b>Level Up — welcome to Level {lvl}!</b>\n"
            f"<i>Your reading earns its rank.</i>\n"
            f"<blockquote>"
            f"🏅 New title: <b>{title_for(lvl)}</b>\n"
            f"🎁 Reward credited: <code>+{level_reward(lvl):g}</code> 💎 BGM"
            f"</blockquote>\n\n")


async def get_progress(uid: int) -> dict:
    """The numbers behind the XP view + profile card."""
    db = await MongoManager.get()
    await _seed_if_missing(db, uid)
    doc = await db.find_one_global("users", {"user_id": uid}, {"xp": 1}) or {}
    xp = float(doc.get("xp") or 0)
    lvl = level_for(xp)
    into = into_level(xp)
    return {
        "xp": int(xp),
        "level": lvl,
        "into": into,
        "need": _LEVEL_STEP,
        "remaining": _LEVEL_STEP - into,
        "bar": progress_bar(into),
        "title": title_for(lvl),
        "next_title": title_for(lvl + 1),
    }
