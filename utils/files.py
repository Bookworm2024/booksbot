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
import re
from datetime import datetime, timezone
from typing import Any

from database.connection import MongoManager

_AUDIO_EXT = {"mp3", "m4b", "m4a", "wav", "ogg", "flac", "aac"}
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


async def index_file(doc: dict[str, Any]) -> bool:
    """Upsert one file. Returns True if newly inserted."""
    db = await MongoManager.get()
    doc.setdefault("indexed_at", datetime.now(timezone.utc))
    return await db.safe_insert("files", doc)


async def search(query: str, *, skip: int = 0, limit: int = 10) -> tuple[list[dict], int]:
    """Return (page, total_matches). Every query word must be in name_lc."""
    words = _norm_words(query)
    if not words:
        return [], 0
    flt = {"$and": [{"name_lc": {"$regex": re.escape(w)}} for w in words]}
    db = await MongoManager.get()
    total = await db.count_global("files", flt)
    page = await db.find_global("files", flt, sort=[("name_lc", 1)],
                                proj={"name": 1, "ext": 1, "kind": 1, "msg_id": 1,
                                      "file_id": 1, "file_unique_id": 1})
    page.sort(key=lambda d: d.get("name_lc") or d.get("name", "").lower())
    return page[skip:skip + limit], total


async def get_file(file_unique_id: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("files", {"file_unique_id": file_unique_id})


async def archive_count() -> int:
    db = await MongoManager.get()
    return await db.count_global("files")
