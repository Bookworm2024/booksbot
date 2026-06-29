"""
utils/foryou.py — AI-driven reading-taste tracking for the 🎯 For You shelf.

Every read (download) and request is tracked by genre. The title's genre comes
from the file's AI tag when it already has one; otherwise the AI engine classifies
it on the fly from the title (and the archive gets tagged as a side effect, so the
work is shared). Per-user tallies live on the user doc:

    genre_reads        {<Genre>: count}   how many reads/requests fell in each genre
    genre_reads_total  int                total classified reads + requests

The user's favourite genre is simply the most-counted one. 🎯 For You then serves
books from that genre and shows how many of their reads/requests matched it.
"""
import logging
from collections import Counter

from database.connection import MongoManager

logger = logging.getLogger(__name__)


async def record_genre_read(uid: int, title: str, *, file_doc: dict | None = None,
                            fuid: str | None = None) -> None:
    """Resolve a read/request's genre and bump the user's tally. Best-effort —
    never raises into the caller, so always fire it with asyncio.create_task().

    Genre source order: an existing file tag (free, no AI) → the AI engine
    classifying the title (only when needed; tags the file for everyone after)."""
    try:
        from utils.ai import classify_genre
        from utils.files import get_file, set_genre
        if file_doc is None and fuid:
            file_doc = await get_file(fuid)
        genre = (file_doc or {}).get("genre")
        if not genre:
            genre = await classify_genre(title)   # None if AI is off / title unknown
            if genre and fuid:
                await set_genre(fuid, genre)       # tag the archive while we're here
        if not genre:
            return
        db = await MongoManager.get()
        await db.safe_update(
            "users", {"user_id": uid},
            {"$inc": {f"genre_reads.{genre}": 1, "genre_reads_total": 1}})
    except Exception:  # noqa: BLE001 — taste tracking must never break a delivery
        logger.debug("record_genre_read failed for %s", uid, exc_info=True)


async def _from_library(uid: int) -> tuple[str | None, int, int]:
    """Fallback taste profile from the genres of the user's downloaded library
    (uses the files' existing AI tags — no extra AI calls). Lets For You work for
    users who read before per-read tallying existed."""
    db = await MongoManager.get()
    lib = await db.find_global("library", {"user_id": uid}, limit=400,
                               proj={"file_unique_id": 1})
    fuids = [x["file_unique_id"] for x in lib if x.get("file_unique_id")]
    if not fuids:
        return None, 0, 0
    files = await db.find_global("files", {"file_unique_id": {"$in": fuids[:400]}},
                                proj={"genre": 1})
    # "Other" is the catch-all bucket, not a real taste signal — don't let it
    # become the user's favourite genre.
    c = Counter(f.get("genre") for f in files if f.get("genre") and f.get("genre") != "Other")
    if not c:
        return None, 0, 0
    genre, count = c.most_common(1)[0]
    return genre, int(count), int(sum(c.values()))


async def favorite_genre(uid: int) -> tuple[str | None, int, int]:
    """(favourite_genre, reads_in_that_genre, total_classified_reads) or (None, 0, 0).

    Prefers the live read/request tally; falls back to the library's genre tags so
    the shelf is useful immediately once the archive is genre-tagged."""
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"genre_reads": 1, "genre_reads_total": 1}) or {}
    reads = u.get("genre_reads") or {}
    # Exclude the "Other" catch-all from the favourite pick (it's not a real taste
    # signal); keep it in the displayed total. Fall back to the library if the only
    # reads so far are "Other".
    picks = {g: n for g, n in reads.items() if g != "Other"}
    if picks:
        genre, count = max(picks.items(), key=lambda kv: kv[1])
        total = int(u.get("genre_reads_total") or sum(reads.values()))
        return genre, int(count), total
    return await _from_library(uid)
