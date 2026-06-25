"""
utils/clubs.py — book clubs / reading rooms (Social).

A club is a lightweight async discussion room around a theme. Users create or
join clubs and post messages other members see. Backed by Mongo:
  clubs         {club_id, name, desc, created_by, created_at, member_count}
  club_members  {club_id, user_id, joined_at}   (unique club_id+user_id)
  club_posts    {post_id, club_id, user_id, name, text, created_at}

Membership join/leave is atomic on the unique index so the member counter can't
drift on a double-tap.
"""
import logging
import random
import string
from datetime import datetime, timezone

from pymongo import DESCENDING

from database.connection import MongoManager

logger = logging.getLogger(__name__)

MAX_POST_LEN = 500
MAX_CLUBS_PER_USER = 5


def _now():
    return datetime.now(timezone.utc)


def _gen(prefix: str) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


async def create_club(name: str, desc: str, by: int) -> str:
    db = await MongoManager.get()
    club_id = _gen("c_")
    await db.safe_insert("clubs", {
        "club_id": club_id, "name": name[:60], "desc": (desc or "")[:300],
        "created_by": by, "created_at": _now(), "member_count": 0,
    })
    await join(club_id, by)  # creator auto-joins
    return club_id


async def created_count(uid: int) -> int:
    db = await MongoManager.get()
    return await db.count_global("clubs", {"created_by": uid})


async def list_clubs(limit: int = 20) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("clubs", {}, limit=limit,
                                sort=[("member_count", DESCENDING)])


async def get_club(club_id: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("clubs", {"club_id": club_id})


async def my_club_ids(uid: int) -> set[str]:
    db = await MongoManager.get()
    rows = await db.find_global("club_members", {"user_id": uid}, proj={"club_id": 1})
    return {r["club_id"] for r in rows if r.get("club_id")}


async def is_member(club_id: str, uid: int) -> bool:
    db = await MongoManager.get()
    return await db.find_one_global(
        "club_members", {"club_id": club_id, "user_id": uid}, {"_id": 1}) is not None


async def join(club_id: str, uid: int) -> bool:
    """Atomic join via the unique (club_id,user_id) index. True if newly joined."""
    db = await MongoManager.get()
    added = await db.safe_insert("club_members",
                                 {"club_id": club_id, "user_id": uid, "joined_at": _now()})
    if added:
        await db.safe_update("clubs", {"club_id": club_id},
                             {"$inc": {"member_count": 1}}, upsert=False)
    return added


async def leave(club_id: str, uid: int) -> bool:
    """True if a membership row was actually removed (so the counter only moves
    once even on a double-tap)."""
    db = await MongoManager.get()
    removed = 0
    for idx in db.healthy:
        res = await db.dbs[idx]["club_members"].delete_one(
            {"club_id": club_id, "user_id": uid})
        removed += res.deleted_count
    if removed:
        await db.safe_update("clubs", {"club_id": club_id},
                             {"$inc": {"member_count": -1}}, upsert=False)
    return removed > 0


async def add_post(club_id: str, uid: int, name: str, text: str) -> str:
    db = await MongoManager.get()
    post_id = _gen("p_")
    await db.safe_insert("club_posts", {
        "post_id": post_id, "club_id": club_id, "user_id": uid,
        "name": (name or "Reader")[:32], "text": (text or "")[:MAX_POST_LEN],
        "created_at": _now(),
    })
    return post_id


async def recent_posts(club_id: str, limit: int = 8) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("club_posts", {"club_id": club_id}, limit=limit,
                                sort=[("created_at", DESCENDING)])
