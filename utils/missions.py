"""
utils/missions.py — daily missions / quests.

A small fixed set of daily tasks. Handlers call mark(uid, key) when the user
does the action; the user claims the accumulated BGM from the missions board.
Progress resets each UTC day. Stored on the user doc:
  missions_day (YYYY-MM-DD), missions_done [keys], missions_claimed [keys]
"""
from datetime import datetime, timezone

from database.connection import MongoManager
from utils.wallet import add_bgm

# key → (label, reward BGM)
MISSIONS = {
    "play_game": ("🎮 Play a game in the arcade", 0.2),
    "download":  ("📚 Add a book to your library", 0.2),
    "spin":      ("🎡 Take a spin on the wheel", 0.1),
}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def mark(uid: int, key: str) -> None:
    """Record a mission as completed today (idempotent). Safe to call anywhere."""
    if key not in MISSIONS:
        return
    try:
        # global XP accrues per action (not once/day) — central hook for the four
        # core actions (play_game / download / spin / claim).
        from utils.xp import award
        await award(uid, key)
        db = await MongoManager.get()
        today = _today()
        # Atomic, self-guarding day reset: only the FIRST action of a new day
        # matches {missions_day != today} and resets — seeding missions_done with
        # THIS key in the same op so its tick isn't lost. A concurrent second
        # action no longer matches and falls through to a plain $addToSet, so a
        # day-rollover race can never clobber a completed mission (cf.
        # challenges.bump / battlepass.bump).
        did_reset = await db.find_one_and_update_global(
            "users", {"user_id": uid, "missions_day": {"$ne": today}},
            {"$set": {"missions_day": today, "missions_done": [key],
                      "missions_claimed": []}})
        if not did_reset:
            await db.safe_update("users", {"user_id": uid},
                                 {"$addToSet": {"missions_done": key}})
        if key == "play_game":
            # daily game-play streak bonus (the "daily challenge"), once/day
            from utils.game_streak import on_game_played
            await on_game_played(uid)
            # weekly tournament: games played this ISO week (resets each week).
            # Atomic week reset (same pattern as the day reset above) so a
            # week-rollover race can't clobber the tournament counter.
            wk = datetime.now(timezone.utc).strftime("%G-W%V")
            did_wreset = await db.find_one_and_update_global(
                "users", {"user_id": uid, "tour_week": {"$ne": wk}},
                {"$set": {"tour_week": wk, "tour_games": 1}})
            if not did_wreset:
                await db.safe_update("users", {"user_id": uid}, {"$inc": {"tour_games": 1}})
        # monthly reading-challenge counters (reset on month change)
        from utils.challenges import bump
        await bump(uid, key)
        # loot-crate key progress (every few actions earns a key)
        from utils.crates import bump as crate_bump
        await crate_bump(uid)
        # seasonal battle-pass points
        from utils.battlepass import bump as bp_bump
        await bp_bump(uid, key)
    except Exception:  # noqa: BLE001 — missions must never break the host action
        pass


async def status(uid: int) -> dict:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid},
                                   {"missions_day": 1, "missions_done": 1,
                                    "missions_claimed": 1}) or {}
    fresh = doc.get("missions_day") == _today()
    done = set(doc.get("missions_done") or []) if fresh else set()
    claimed = set(doc.get("missions_claimed") or []) if fresh else set()
    claimable = round(sum(MISSIONS[k][1] for k in (done - claimed) if k in MISSIONS), 3)
    return {"done": done, "claimed": claimed, "claimable": claimable}


async def claim(uid: int) -> float:
    db = await MongoManager.get()
    today = _today()
    # Atomic per-key: each mission is banked exactly once. The $ne-guarded
    # $addToSet means a double-tap (two concurrent claims) can't pay any mission
    # twice — only the call that actually adds the key counts its reward.
    paid = 0.0
    for key, (_label, reward) in MISSIONS.items():
        updated = await db.find_one_and_update_global(
            "users",
            {"user_id": uid, "missions_day": today,
             "missions_done": key, "missions_claimed": {"$ne": key}},
            {"$addToSet": {"missions_claimed": key}})
        if updated:
            paid += reward
    paid = round(paid, 3)
    if paid > 0:
        await add_bgm(uid, paid)
    return paid
