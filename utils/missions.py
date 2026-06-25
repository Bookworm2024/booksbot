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
    "play_game": ("🎮 Play a game", 0.2),
    "download":  ("📥 Download a book", 0.2),
    "spin":      ("🎡 Spin the wheel", 0.1),
    "claim":     ("🪙 Claim daily BCN", 0.1),
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
        doc = await db.find_one_global("users", {"user_id": uid}, {"missions_day": 1})
        if not doc or doc.get("missions_day") != today:
            await db.safe_update("users", {"user_id": uid},
                                 {"$set": {"missions_day": today, "missions_done": [key],
                                           "missions_claimed": []}})
        else:
            await db.safe_update("users", {"user_id": uid},
                                 {"$addToSet": {"missions_done": key}})
        if key == "play_game":
            # daily game-play streak bonus (the "daily challenge"), once/day
            from utils.game_streak import on_game_played
            await on_game_played(uid)
            # weekly tournament: games played this ISO week (resets each week)
            wk = datetime.now(timezone.utc).strftime("%G-W%V")
            tdoc = await db.find_one_global("users", {"user_id": uid}, {"tour_week": 1})
            if not tdoc or tdoc.get("tour_week") != wk:
                await db.safe_update("users", {"user_id": uid},
                                     {"$set": {"tour_week": wk, "tour_games": 1}})
            else:
                await db.safe_update("users", {"user_id": uid}, {"$inc": {"tour_games": 1}})
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
    st = await status(uid)
    if st["claimable"] <= 0:
        return 0.0
    await add_bgm(uid, st["claimable"])
    # mark every completed mission as claimed
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"missions_claimed": list(st["done"])}})
    return st["claimable"]
