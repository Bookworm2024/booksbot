"""
utils/shelf.py — "books finished" shelf + per-book notes (Reader, Pillar 1).

  finished  {user_id, file_unique_id, name, ext, chan_id, msg_id, file_id, at}
            unique (user_id, file_unique_id) — a book is finished at most once
  notes     {note_id, user_id, file_unique_id, name, text, at}

Delivery fields are copied onto the finished doc (like favorites) so a finished
book can be re-fetched even if the live channel later changes.
"""
import logging
import random
import string
from datetime import datetime, timezone

from pymongo import DESCENDING

from database.connection import MongoManager

logger = logging.getLogger(__name__)

MAX_NOTE_LEN = 600


def _now():
    return datetime.now(timezone.utc)


def _deliverable(f: dict) -> dict:
    return {"name": f.get("name"), "ext": f.get("ext"), "kind": f.get("kind"),
            "chan_id": f.get("chan_id"), "msg_id": f.get("msg_id"),
            "file_id": f.get("file_id")}


# ── finished shelf ────────────────────────────────────────────────────────────
async def mark_finished(uid: int, f: dict) -> bool:
    """True if newly marked finished (idempotent on the unique index)."""
    db = await MongoManager.get()
    return await db.safe_insert("finished", {
        "user_id": uid, "file_unique_id": f.get("file_unique_id"),
        **_deliverable(f), "at": _now()})


async def unmark_finished(uid: int, fuid: str) -> None:
    db = await MongoManager.get()
    for idx in db.healthy:
        await db.dbs[idx]["finished"].delete_one(
            {"user_id": uid, "file_unique_id": fuid})


async def is_finished(uid: int, fuid: str) -> bool:
    db = await MongoManager.get()
    return await db.find_one_global(
        "finished", {"user_id": uid, "file_unique_id": fuid}, {"_id": 1}) is not None


async def finished_list(uid: int, limit: int = 30) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("finished", {"user_id": uid}, limit=limit,
                                sort=[("at", DESCENDING)])


async def finished_count(uid: int) -> int:
    db = await MongoManager.get()
    return await db.count_global("finished", {"user_id": uid})


async def get_finished(uid: int, fuid: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("finished", {"user_id": uid, "file_unique_id": fuid})


# ── per-book notes ────────────────────────────────────────────────────────────
async def add_note(uid: int, f: dict, text: str) -> str:
    db = await MongoManager.get()
    note_id = "n_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    await db.safe_insert("notes", {
        "note_id": note_id, "user_id": uid, "file_unique_id": f.get("file_unique_id"),
        "name": f.get("name"), "text": (text or "")[:MAX_NOTE_LEN], "at": _now()})
    return note_id


async def notes_for(uid: int, fuid: str) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("notes", {"user_id": uid, "file_unique_id": fuid},
                                sort=[("at", DESCENDING)])


async def delete_note(uid: int, note_id: str) -> None:
    db = await MongoManager.get()
    for idx in db.healthy:
        await db.dbs[idx]["notes"].delete_one({"user_id": uid, "note_id": note_id})


async def books_with_notes(uid: int) -> list[dict]:
    """Distinct books the user has noted, with note counts (most-recent first)."""
    db = await MongoManager.get()
    agg: dict[str, dict] = {}
    for idx in db.healthy:
        cur = db.dbs[idx]["notes"].aggregate([
            {"$match": {"user_id": uid}},
            {"$group": {"_id": "$file_unique_id",
                        "name": {"$first": "$name"},
                        "count": {"$sum": 1},
                        "last": {"$max": "$at"}}},
        ])
        async for row in cur:
            fuid = row.get("_id")
            if not fuid:
                continue
            cur_e = agg.get(fuid)
            if cur_e:
                cur_e["count"] += int(row.get("count") or 0)
            else:
                agg[fuid] = {"fuid": fuid, "name": row.get("name") or "Untitled",
                             "count": int(row.get("count") or 0), "last": row.get("last")}
    return sorted(agg.values(), key=lambda d: d.get("last") or _now(), reverse=True)
