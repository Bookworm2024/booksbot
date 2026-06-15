"""
utils/game_streak.py — daily game-play streak bonus (the "daily challenge").

The first game a user plays each day bumps their game-play streak and pays an
escalating bonus (once/day, atomic via the day-flip). Wired into utils.missions
.mark("play_game"), which every game already calls — so it covers quiz, T/F, the
book MCQs, Bookle, Hangman and Anagram from one place.
"""
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager
from utils.wallet import add_bgm

# bonus by consecutive-day streak (day 1..5; 5+ keeps the day-5 amount)
_BONUS = [0.05, 0.10, 0.15, 0.20, 0.30]


def _d(offset: int = 0) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).strftime("%Y-%m-%d")


async def on_game_played(uid: int) -> dict:
    """Award the daily game-streak bonus on the first game of the day. Returns
    {streak, bonus} when paid, or {} if already counted today."""
    db = await MongoManager.get()
    today, yesterday = _d(0), _d(-1)
    # atomic: only the first game today flips game_streak_day → pays once/day
    before = await db.find_one_and_update_global(
        "users", {"user_id": uid, "game_streak_day": {"$ne": today}},
        {"$set": {"game_streak_day": today}}, return_before=True)
    if before is None:
        return {}
    prev = int(before.get("game_streak") or 0)
    streak = prev + 1 if before.get("game_streak_day") == yesterday else 1
    bonus = _BONUS[min(streak, len(_BONUS)) - 1]
    await db.safe_update("users", {"user_id": uid}, {"$set": {"game_streak": streak}})
    await add_bgm(uid, bonus)
    return {"streak": streak, "bonus": bonus}
