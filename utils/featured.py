"""
utils/featured.py — sponsored / featured book slots (paid placement).

An admin features a file for N days (sell the slot). Featured titles surface in
a ⭐ Featured section in Discover. Stored as `featured_until` on the file doc.
"""
from datetime import datetime, timedelta, timezone

from pymongo import DESCENDING

from database.connection import MongoManager


def _now():
    return datetime.now(timezone.utc)


async def add_featured(file_unique_id: str, days: float = 7) -> None:
    db = await MongoManager.get()
    await db.safe_update("files", {"file_unique_id": file_unique_id},
                         {"$set": {"featured_until": _now() + timedelta(days=days)}},
                         upsert=False)


async def remove_featured(file_unique_id: str) -> None:
    db = await MongoManager.get()
    await db.safe_update("files", {"file_unique_id": file_unique_id},
                         {"$set": {"featured_until": None}}, upsert=False)


async def featured_files(limit: int = 10) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global(
        "files", {"featured_until": {"$gt": _now()}}, limit=limit,
        sort=[("featured_until", DESCENDING)],
        proj={"name": 1, "ext": 1, "kind": 1, "file_unique_id": 1, "featured_until": 1})
