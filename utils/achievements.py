"""
utils/achievements.py — unlockable achievements (goals + push on unlock).

Each achievement is reached when a user stat crosses a target. check_unlocks()
awards the newly-reached ones (stored on the user, deduped) and DMs the user;
board() renders all with locked/unlocked + progress. Stats come from the user
doc (downloads / games_played / ref_count / login_streak) or the favorites count.
"""
from database.connection import MongoManager

# id · display name · description · stat key · target
ACHIEVEMENTS = [
    {"id": "first_dl",  "name": "📖 First Page",   "desc": "Download your first book", "metric": "downloads",     "target": 1},
    {"id": "reader_10", "name": "📖 Bookworm",     "desc": "Download 10 books",        "metric": "downloads",     "target": 10},
    {"id": "reader_50", "name": "📚 Avid Reader",  "desc": "Download 50 books",        "metric": "downloads",     "target": 50},
    {"id": "reader_100","name": "🏆 Master Reader","desc": "Download 100 books",       "metric": "downloads",     "target": 100},
    {"id": "gamer_10",  "name": "🎮 Gamer",        "desc": "Play 10 games",            "metric": "games_played",  "target": 10},
    {"id": "gamer_50",  "name": "🕹 Game Master",  "desc": "Play 50 games",            "metric": "games_played",  "target": 50},
    {"id": "ref_5",     "name": "🤝 Connector",    "desc": "Refer 5 friends",          "metric": "ref_count",     "target": 5},
    {"id": "ref_25",    "name": "🌟 Influencer",   "desc": "Refer 25 friends",         "metric": "ref_count",     "target": 25},
    {"id": "streak_7",  "name": "🔥 Regular",      "desc": "7-day login streak",       "metric": "login_streak",  "target": 7},
    {"id": "streak_30", "name": "🔥 Devoted",      "desc": "30-day login streak",      "metric": "login_streak",  "target": 30},
    {"id": "fav_10",    "name": "⭐ Curator",      "desc": "Favorite 10 books",        "metric": "favorites",     "target": 10},
]
_BY_ID = {a["id"]: a for a in ACHIEVEMENTS}


def _value(d: dict, favs: int, metric: str) -> int:
    if metric == "favorites":
        return favs
    return int(d.get(metric) or 0)


def _earned_ids(d: dict, favs: int) -> set:
    return {a["id"] for a in ACHIEVEMENTS if _value(d, favs, a["metric"]) >= a["target"]}


async def _fav_count(db, uid: int) -> int:
    return await db.count_global("favorites", {"user_id": uid})


async def check_unlocks(bot, uid: int) -> set:
    """Award newly-earned achievements and DM the user. Returns the new ids."""
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": uid}) or {}
    favs = await _fav_count(db, uid)
    have = set(d.get("achievements") or [])
    now = _earned_ids(d, favs)
    new = now - have
    if not new:
        return set()
    # Additive + race-safe: only push the NEW ids. A whole-array $set would let
    # two concurrent stat-changing callers clobber each other's unlocks. board()
    # recomputes earned ids from stats, so the stored array need not be complete.
    await db.safe_update("users", {"user_id": uid},
                         {"$addToSet": {"achievements": {"$each": sorted(new)}}})
    for aid in new:
        a = _BY_ID.get(aid)
        if not a:
            continue
        try:
            await bot.send_message(
                uid,
                "🏆 <b>Achievement Unlocked</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"<blockquote>{a['name']}\n"
                f"<i>{a['desc']}</i></blockquote>\n"
                "<i>✨ Beautifully done — it's now pinned to your trophy shelf. "
                "More milestones are waiting.</i>")
        except Exception:  # noqa: BLE001 — user may have blocked the bot
            pass
    return new


async def board(uid: int) -> str:
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": uid}) or {}
    favs = await _fav_count(db, uid)
    got = _earned_ids(d, favs)
    lines = []
    for a in ACHIEVEMENTS:
        if a["id"] in got:
            lines.append(f"✅ <b>{a['name']}</b> — {a['desc']}")
        else:
            cur = min(_value(d, favs, a["metric"]), a["target"])
            lines.append(
                f"🔒 {a['name']} — <i>{a['desc']}</i> · <code>{cur}/{a['target']}</code>")
    remaining = len(ACHIEVEMENTS) - len(got)
    if remaining <= 0:
        footer = ("\n\n<i>👑 Every trophy claimed — you've completed the full "
                  "collection. A true Books Provider legend.</i>")
    else:
        footer = ("\n\n<i>💡 Each milestone unlocks on its own as you read, play "
                  f"and invite — {remaining} still to earn.</i>")
    return (f"🏆 <b>Your Achievements</b>\n"
            f"<i>{len(got)} of {len(ACHIEVEMENTS)} trophies unlocked — your reading "
            "legacy, one badge at a time.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"
            + footer)
