"""
utils/dedupe.py — archive duplicate detection (admin content tool).

Finds files that share the same normalized title (name_lc) — the kind of
duplicate the indexer's (chan_id,msg_id)/file_unique_id guards can't catch (e.g.
the same book posted twice as different messages). Admin-reviewed, never
automatic, since same-title files can be legitimately different editions.
"""
import logging

from database.connection import MongoManager

logger = logging.getLogger(__name__)


async def duplicate_groups(limit: int = 15) -> list[dict]:
    """Top title-duplicate groups across the whole archive (merged across
    clusters). Each: {name, name_lc, count, ids:[file_unique_id...]}."""
    db = await MongoManager.get()
    merged: dict[str, dict] = {}
    for idx in db.healthy:
        cur = db.dbs[idx]["files"].aggregate([
            {"$match": {"name_lc": {"$exists": True, "$ne": ""}}},
            {"$group": {"_id": "$name_lc",
                        "ids": {"$addToSet": "$file_unique_id"},
                        "name": {"$first": "$name"}}},
        ])
        async for row in cur:
            key = row.get("_id")
            if not key:
                continue
            g = merged.setdefault(key, {"name_lc": key, "name": row.get("name") or key,
                                        "ids": set()})
            g["ids"].update(i for i in (row.get("ids") or []) if i)
    groups = [{"name": g["name"], "name_lc": g["name_lc"],
               "ids": sorted(g["ids"]), "count": len(g["ids"])}
              for g in merged.values() if len(g["ids"]) > 1]
    groups.sort(key=lambda g: g["count"], reverse=True)
    return groups[:limit]


async def total_duplicates() -> int:
    """How many extra (removable) duplicate files exist = sum(count-1)."""
    groups = await duplicate_groups(limit=10_000)
    return sum(g["count"] - 1 for g in groups)


async def _delete(db, fuid: str) -> None:
    for idx in db.healthy:
        await db.dbs[idx]["files"].delete_one({"file_unique_id": fuid})


async def clean_group(name_lc: str) -> int:
    """Keep the BEST file for `name_lc` (prefers one with both a delivery msg_id
    and a bot file_id), delete the rest. Returns how many were removed."""
    db = await MongoManager.get()
    rows = await db.find_global("files", {"name_lc": name_lc},
                                proj={"file_unique_id": 1, "file_id": 1, "msg_id": 1,
                                      "chan_id": 1, "indexed_at": 1})
    if len(rows) <= 1:
        return 0

    def score(f: dict) -> tuple:
        return (1 if (f.get("chan_id") and f.get("msg_id")) else 0,
                1 if f.get("file_id") else 0)

    rows.sort(key=score, reverse=True)
    keep = rows[0]
    removed = 0
    for f in rows[1:]:
        if f.get("file_unique_id") and f["file_unique_id"] != keep.get("file_unique_id"):
            await _delete(db, f["file_unique_id"])
            removed += 1
    return removed
