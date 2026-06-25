"""
utils/quests.py — one-time growth quests (share-to-earn / invite quests).

Distinct from the daily missions and the auto-paying referral milestones: these
are lifetime, one-shot growth goals (share the bot, refer N friends, hit a level,
play N games) that each pay a BGM bounty once. Claims are atomic and tracked in
`quests_claimed` on the user doc.
"""
import logging

from database.connection import MongoManager
from utils.wallet import add_bgm
from utils.xp import level_for

logger = logging.getLogger(__name__)

# (key, emoji, title, desc, metric, target, reward BGM)
#   metric "share" is a one-shot flag set when the user uses the share feature;
#   "level" is derived from XP; the rest are plain user-doc counters.
QUESTS = [
    ("share",   "📣", "Spread the Word", "Share the bot with a friend", "share",         1,  0.5),
    ("invite1", "🤝", "First Invite",     "Refer 1 friend",              "ref_count",     1,  1.0),
    ("invite3", "👥", "Squad Builder",    "Refer 3 friends",             "ref_count",     3,  2.0),
    ("invite7", "🌟", "Influencer",       "Refer 7 friends",             "ref_count",     7,  5.0),
    ("level5",  "📈", "Rising Reader",    "Reach Level 5",               "level",         5,  2.0),
    ("play25",  "🎮", "Game Buff",        "Play 25 games",               "games_played",  25, 2.0),
]


def _have(doc: dict, metric: str) -> int:
    if metric == "share":
        return 1 if doc.get("quest_shared") else 0
    if metric == "level":
        return level_for(doc.get("xp") or 0)
    return int(doc.get(metric) or 0)


async def status(uid: int) -> list[dict]:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}) or {}
    claimed = set(doc.get("quests_claimed") or [])
    out = []
    for qkey, emoji, title, desc, metric, target, reward in QUESTS:
        have = _have(doc, metric)
        done = have >= target
        out.append({
            "key": qkey, "emoji": emoji, "title": title, "desc": desc,
            "metric": metric, "have": min(have, target), "target": target,
            "reward": reward, "done": done, "claimed": qkey in claimed,
            "claimable": done and qkey not in claimed,
        })
    return out


async def mark_shared(uid: int) -> None:
    """Flag that the user used the share feature (unlocks the share quest)."""
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid}, {"$set": {"quest_shared": True}})


async def claim(uid: int, qkey: str) -> float:
    """Pay a completed quest exactly once. Returns BGM paid (0 if not eligible)."""
    spec = next((q for q in QUESTS if q[0] == qkey), None)
    if not spec:
        return 0.0
    _k, _e, _t, _d, metric, target, reward = spec
    db = await MongoManager.get()
    # re-check completion from the live doc
    doc = await db.find_one_global("users", {"user_id": uid}) or {}
    if _have(doc, metric) < target:
        return 0.0
    # atomic: addToSet only when the key isn't already claimed → no double pay
    updated = await db.find_one_and_update_global(
        "users", {"user_id": uid, "quests_claimed": {"$ne": qkey}},
        {"$addToSet": {"quests_claimed": qkey}})
    if not updated:
        return 0.0
    await add_bgm(uid, reward)
    return reward
