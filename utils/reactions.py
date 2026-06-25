"""
utils/reactions.py — quick emoji reactions on titles (Social).

One reaction per (user, file): tapping a different emoji switches it, tapping the
same one removes it. Counts aggregate across clusters. Backed by Mongo
`reactions` with a unique (file_unique_id, user_id) index.
"""
from datetime import datetime, timezone

from database.connection import MongoManager

REACTIONS = ["👍", "❤️", "🔥", "😂", "😮"]


def _now():
    return datetime.now(timezone.utc)


async def toggle(fuid: str, uid: int, emoji: str) -> str | None:
    """Set/switch/clear the user's reaction. Returns the new emoji, or None if it
    was removed (tapped the one they already had)."""
    if emoji not in REACTIONS:
        return None
    db = await MongoManager.get()
    existing = await db.find_one_global("reactions",
                                        {"file_unique_id": fuid, "user_id": uid})
    if existing:
        if existing.get("emoji") == emoji:
            for idx in db.healthy:
                await db.dbs[idx]["reactions"].delete_one(
                    {"file_unique_id": fuid, "user_id": uid})
            return None
        await db.safe_update("reactions", {"file_unique_id": fuid, "user_id": uid},
                             {"$set": {"emoji": emoji, "at": _now()}}, upsert=False)
        return emoji
    await db.safe_update(
        "reactions", {"file_unique_id": fuid, "user_id": uid},
        {"$set": {"file_unique_id": fuid, "user_id": uid, "emoji": emoji, "at": _now()}},
        upsert=True)
    return emoji


async def counts(fuid: str) -> dict:
    db = await MongoManager.get()
    out: dict[str, int] = {}
    for idx in db.healthy:
        cur = db.dbs[idx]["reactions"].aggregate(
            [{"$match": {"file_unique_id": fuid}},
             {"$group": {"_id": "$emoji", "n": {"$sum": 1}}}])
        async for row in cur:
            emo = row.get("_id")
            if emo:
                out[emo] = out.get(emo, 0) + int(row.get("n") or 0)
    return out


async def user_reaction(fuid: str, uid: int) -> str | None:
    db = await MongoManager.get()
    doc = await db.find_one_global("reactions",
                                   {"file_unique_id": fuid, "user_id": uid}, {"emoji": 1})
    return doc.get("emoji") if doc else None
