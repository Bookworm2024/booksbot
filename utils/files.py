"""
utils/files.py — the searchable file archive.

A `files` doc:
  file_unique_id  str   stable dedupe key
  name            str   clean display title
  name_lc         str   lowercase, for matching
  ext             str   "pdf" / "epub" / "mp3" ...
  kind            str   "document" | "audio" | "video" | "photo"
  msg_id          int   message id in FILE_CHANNEL_ID (delivery via copy_message)
  file_id         str?  bot-usable file_id (only present for live-indexed files)
  caption         str?
  indexed_at      datetime

Search uses an all-words substring match (every query word must appear in the
title) — predictable and matches the original bot's behaviour. At 30k docs this
is comfortably fast; the `name_lc` index plus the text index back it up.
"""
import difflib
import re
from datetime import datetime, timezone
from typing import Any

from pymongo import DESCENDING

from database.connection import MongoManager

_AUDIO_EXT = {"mp3", "m4b", "m4a", "wav", "ogg", "flac", "aac"}
_MAX_SCAN = 500  # cap matches materialised per search (memory bound)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SEARCH_PROJ = {"name": 1, "name_lc": 1, "ext": 1, "kind": 1, "msg_id": 1,
                "file_id": 1, "file_unique_id": 1, "indexed_at": 1, "dl_count": 1}
_TAG_RE = re.compile(r"@\w+")
_CLEAN_RE = re.compile(r"[_\-.]+")
_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def clean_title(raw: str) -> str:
    """@mentions out, separators → spaces, collapse whitespace."""
    name = _TAG_RE.sub("", raw or "")
    name = _CLEAN_RE.sub(" ", name)
    return " ".join(name.split()).strip()


def kind_for_ext(ext: str) -> str:
    return "audio" if ext.lower() in _AUDIO_EXT else "document"


def icon_for(ext: str) -> str:
    e = ext.lower()
    if e == "pdf":
        return "📄"
    if e == "epub":
        return "📘"
    if e == "mobi":
        return "📙"
    if e in _AUDIO_EXT:
        return "🎧"
    if e in ("zip", "rar", "cbz", "cbr"):
        return "📦"
    return "📁"


def _norm_words(q: str) -> list[str]:
    q = _NORM_RE.sub(" ", (q or "").lower())
    return [w for w in q.split() if len(w) >= 2]


def trigrams(text: str) -> list[str]:
    """Sorted set of 3-grams over the alphanumeric-only, lowercased text.
    Powers typo-tolerant search (handles internal edits like hobit→hobbit)."""
    s = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    if len(s) < 3:
        return [s] if s else []
    return sorted({s[i:i + 3] for i in range(len(s) - 2)})


async def index_file(doc: dict[str, Any]) -> bool:
    """Upsert one file. Returns True if newly inserted. Stamps indexed_at and a
    trigram index of the title (for fuzzy search) if missing."""
    db = await MongoManager.get()
    doc.setdefault("indexed_at", datetime.now(timezone.utc))
    if "name_tg" not in doc:
        doc["name_tg"] = trigrams(doc.get("name_lc") or doc.get("name", ""))
    return await db.safe_insert("files", doc)


def _ftype_clause(ftype: str | None) -> dict | None:
    if ftype == "audio":
        return {"kind": "audio"}
    if ftype in ("pdf", "epub", "mobi"):
        return {"ext": ftype}
    return None


def _sort_rows(rows: list[dict], sort: str, words: list[str]) -> None:
    """In-place sort of a materialised result page."""
    if sort == "new":
        rows.sort(key=lambda d: d.get("indexed_at") or _EPOCH, reverse=True)
    elif sort == "popular":
        rows.sort(key=lambda d: d.get("dl_count") or 0, reverse=True)
    else:  # relevance: shorter, tighter titles first (usually the real book)
        rows.sort(key=lambda d: (len(d.get("name_lc") or ""), d.get("name_lc") or ""))


async def search(query: str, *, skip: int = 0, limit: int = 10,
                 ftype: str | None = None, sort: str = "relevance") -> tuple[list[dict], int]:
    """Exact all-words search: every query word must appear in name_lc.
    Optional ftype ('pdf'/'epub'/'mobi'/'audio') and sort
    ('relevance'/'new'/'popular'). Returns (page, total_matches)."""
    words = _norm_words(query)
    if not words:
        return [], 0
    clauses = [{"name_lc": {"$regex": re.escape(w)}} for w in words]
    extra = _ftype_clause(ftype)
    if extra:
        clauses.append(extra)
    flt = {"$and": clauses}
    db = await MongoManager.get()
    total = await db.count_global("files", flt)
    # Bound memory: only materialise up to _MAX_SCAN matches for pagination.
    rows = await db.find_global("files", flt, limit=_MAX_SCAN, proj=_SEARCH_PROJ)
    _sort_rows(rows, sort, words)
    return rows[skip:skip + limit], total


def _fuzzy_score(qn: str, name_lc: str, words: list[str]) -> float:
    """0..~1 similarity. Combines whole-string ratio with the best per-word
    match, so misspelled / reordered / partial queries still rank."""
    full = difflib.SequenceMatcher(None, qn, name_lc).ratio()
    name_words = name_lc.split() or [name_lc]
    per = 0.0
    for w in words:
        per += max((difflib.SequenceMatcher(None, w, nw).ratio() for nw in name_words),
                   default=0.0)
    per /= max(1, len(words))
    return max(full, per)


async def fuzzy_search(query: str, *, skip: int = 0, limit: int = 10,
                       ftype: str | None = None) -> tuple[list[dict], int]:
    """Typo-tolerant fallback. Builds a candidate pool (any query word, or a
    word-prefix, appears in the title) then re-ranks by fuzzy similarity.
    Used when exact search returns nothing."""
    words = _norm_words(query)
    if not words:
        return [], 0
    ors: list[dict] = []
    for w in words:
        ors.append({"name_lc": {"$regex": re.escape(w)}})
        if len(w) >= 4:   # tolerate a trailing typo / plural
            ors.append({"name_lc": {"$regex": re.escape(w[: max(3, len(w) - 1)])}})
    # trigram candidates catch internal typos (hobit→hobbit) that substrings miss
    qtg = trigrams(" ".join(words))
    if qtg:
        ors.append({"name_tg": {"$in": qtg}})
    flt: dict = {"$or": ors}
    extra = _ftype_clause(ftype)
    if extra:
        flt = {"$and": [flt, extra]}
    db = await MongoManager.get()
    rows = await db.find_global("files", flt, limit=_MAX_SCAN, proj=_SEARCH_PROJ)
    qn = " ".join(words)
    scored = []
    for d in rows:
        score = _fuzzy_score(qn, (d.get("name_lc") or d.get("name", "").lower()), words)
        if score >= 0.45:
            scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [d for _, d in scored]
    return ranked[skip:skip + limit], len(ranked)


async def get_file(file_unique_id: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("files", {"file_unique_id": file_unique_id})


async def archive_count() -> int:
    db = await MongoManager.get()
    return await db.count_global("files")


_DISC_PROJ = {"name": 1, "ext": 1, "kind": 1, "file_unique_id": 1, "dl_count": 1}


async def recent_files(limit: int = 48) -> list[dict]:
    """Newest-indexed files (New Arrivals)."""
    from pymongo import DESCENDING
    db = await MongoManager.get()
    return await db.find_global("files", {}, limit=limit,
                                sort=[("indexed_at", DESCENDING)], proj=_DISC_PROJ)


async def popular_files(limit: int = 48) -> list[dict]:
    """Most-downloaded files (all-time)."""
    from pymongo import DESCENDING
    db = await MongoManager.get()
    return await db.find_global("files", {"dl_count": {"$gt": 0}}, limit=limit,
                                sort=[("dl_count", DESCENDING)], proj=_DISC_PROJ)


async def bump_download(file_unique_id: str) -> None:
    db = await MongoManager.get()
    await db.safe_update("files", {"file_unique_id": file_unique_id},
                         {"$inc": {"dl_count": 1}}, upsert=False)


async def book_of_the_day(day_index: int) -> dict | None:
    """Deterministic daily pick from a bounded recent window."""
    pool = await recent_files(limit=200)
    if not pool:
        return None
    return pool[day_index % len(pool)]


# ── genres (AI-tagged) ──────────────────────────────────────────────────────
GENRES = ["Fiction", "Sci-Fi", "Fantasy", "Mystery", "Thriller", "Romance",
          "Horror", "Non-Fiction", "Self-Help", "Biography", "History",
          "Business", "Science", "Children", "Poetry", "Other"]


async def set_genre(file_unique_id: str, genre: str) -> None:
    db = await MongoManager.get()
    await db.safe_update("files", {"file_unique_id": file_unique_id},
                         {"$set": {"genre": genre}}, upsert=False)


async def untagged_files(limit: int = 25) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("files", {"genre": {"$exists": False}}, limit=limit,
                                proj={"name": 1, "file_unique_id": 1})


async def untagged_count() -> int:
    db = await MongoManager.get()
    return await db.count_global("files", {"genre": {"$exists": False}})


async def files_by_genre(genre: str, limit: int = 48) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("files", {"genre": genre}, limit=limit,
                                sort=[("dl_count", DESCENDING)], proj=_DISC_PROJ)
