"""
utils/ratings.py — per-title ratings & reviews (Goodreads-style).

One rating per (user, file): upserting replaces it. summary() aggregates the
average + count across clusters; recent_reviews() lists the latest text reviews.
"""
from datetime import datetime, timezone

from pymongo import DESCENDING

from database.connection import MongoManager


def _now():
    return datetime.now(timezone.utc)


async def set_rating(uid: int, fuid: str, stars: int, name: str = "") -> None:
    db = await MongoManager.get()
    await db.safe_update(
        "ratings", {"user_id": uid, "file_unique_id": fuid},
        {"$set": {"user_id": uid, "file_unique_id": fuid, "stars": int(stars),
                  "name": name, "rated_at": _now()}}, upsert=True)


async def set_review(uid: int, fuid: str, text: str) -> None:
    db = await MongoManager.get()
    await db.safe_update(
        "ratings", {"user_id": uid, "file_unique_id": fuid},
        {"$set": {"review": (text or "")[:500], "rated_at": _now()}}, upsert=True)


async def user_rating(uid: int, fuid: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("ratings", {"user_id": uid, "file_unique_id": fuid})


async def summary(fuid: str) -> tuple[float, int]:
    """(average_stars, count) for a file, aggregated across clusters."""
    db = await MongoManager.get()
    total, count = 0, 0
    for idx in db.healthy:
        cur = db.dbs[idx]["ratings"].aggregate(
            [{"$match": {"file_unique_id": fuid}},
             {"$group": {"_id": None, "s": {"$sum": "$stars"}, "n": {"$sum": 1}}}])
        async for row in cur:
            total += int(row.get("s") or 0)
            count += int(row.get("n") or 0)
    return (round(total / count, 2) if count else 0.0), count


async def recent_reviews(fuid: str, limit: int = 5) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("ratings",
                                {"file_unique_id": fuid, "review": {"$exists": True, "$ne": ""}},
                                limit=limit, sort=[("rated_at", DESCENDING)],
                                proj={"stars": 1, "review": 1, "name": 1})


def stars_bar(avg: float) -> str:
    full = int(round(avg))
    return "⭐" * full + "☆" * (5 - full)
