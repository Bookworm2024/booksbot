"""
utils/harvester.py — automated public-domain book harvester.

A light background worker that grows the archive on its own. It pulls *latest
arrivals* and back-fills titles not yet in our database from the biggest
PUBLIC-DOMAIN archives — all legal to redistribute:
  • Project Gutenberg (Gutendex JSON API) ......... EPUB ebooks
  • Standard Ebooks (atom new-releases feed) ...... EPUB ebooks
  • Internet Archive (advancedsearch, PD-filtered)  EPUB + PDF ebooks (~1M items)
  • LibriVox (audiobooks API, archive.org-hosted) . audiobooks (per chapter)
Each file is uploaded to the bot's database (file) channel and indexed so the
normal search → `dl:` delivery + watchlist all work unchanged. Users searching a
title get it automatically once it lands; anyone on the watchlist for it is pinged.

FORMAT POLICY: only **PDF, EPUB and audiobooks** are ever ingested — never txt,
mobi, html or anything else. The `_ALLOWED_EXT` guard enforces this at ingest no
matter the source.

Design goals:
  • LIGHT — one file per tick, paced (harvest_interval_sec, ~75s default). No
    weekly cap by default (harvest_weekly_cap 0 = unlimited; set >0 to throttle).
    Idles long when caught up. Sources are visited round-robin so ebooks, PDFs
    and audiobooks all flow rather than one source starving the rest.
  • SAFE — public-domain only (Internet Archive is filtered to
    possible-copyright-status:NOT_IN_COPYRIGHT; LibriVox is 100% PD); size-capped
    downloads; never crashes the bot.
  • SMART — dedupes against the existing archive by source id AND normalized
    title (so we never duplicate the 30k legacy files); genre comes free from
    each source's own subject tags, falling back to the AI engine only when unclear.
  • QUIET — fully background; admins get ONE weekly digest (7-day timer), not
    per-file spam.

Audiobooks: LibriVox recordings are multi-hour, so a whole book can't be sent as
one Telegram file (Bot-API upload cap). We therefore harvest **one file per
chapter** ("<Title> — Part NN"), each a complete, streamable file under the size
cap; the user browses the chapters via the search pick-list.

All cursors/counters live in Mongo kv (single-writer loop → plain read-modify is
safe). Requires the file channel to be set (Admin → 🗂 File Channel) and the bot
to be an admin of it; otherwise it simply idles.
"""
import asyncio
import contextlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html import escape
from urllib.parse import quote

import aiohttp
from aiogram.types import BufferedInputFile

from config import ADMIN_IDS, SUPER_ADMIN_ID
from database.connection import MongoManager
from utils.channel import get_file_channel
from utils.files import (_AUDIO_EXT, clean_title, extract_from_message,
                         index_file, trigrams)
from utils.settings import get_float

logger = logging.getLogger(__name__)

GUTENDEX = "https://gutendex.com/books/"
# Standard Ebooks' OPDS feed is now Patron-gated (401); the atom new-releases feed
# is public and carries direct .epub download links.
SE_FEED = "https://standardebooks.org/feeds/atom/new-releases"
# Internet Archive advancedsearch — newest public-domain texts that actually carry
# a downloadable EPUB or PDF derivative (the format facet filters out scan-only
# Google items). possible-copyright-status:NOT_IN_COPYRIGHT keeps it legal.
IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/"
IA_DOWNLOAD = "https://archive.org/download/"
IA_ROWS = 50
# LibriVox audiobooks API (public-domain recordings hosted on archive.org).
LV_FEED = "https://librivox.org/api/feed/audiobooks/"
LV_ROWS = 12
_SE_REFETCH_SEC = 3600   # Standard Ebooks new-releases feed is tiny; poll hourly
_UA = {"User-Agent": "BooksBot/1.0 (public-domain archive harvester)"}

# Gutendex mime prefix → extension. Gutenberg exposes EPUB (best in the reader);
# it does NOT offer PDF, and we deliberately DROP txt/mobi/html. PDFs come from
# the Internet Archive source instead.
_FORMATS = [
    ("application/epub+zip", "epub"),
]

# The ONLY extensions the harvester will ever ingest, regardless of source.
_EBOOK_EXT = {"pdf", "epub"}
_ALLOWED_EXT = _EBOOK_EXT | _AUDIO_EXT

# Per-tick budget of source-metadata fetches (Internet Archive / LibriVox resolve
# a download URL by fetching an item's metadata) — keeps every tick light.
_RESOLVE_BUDGET = 4

_SRC_NAME = {"gutenberg": "Project Gutenberg", "standardebooks": "Standard Ebooks",
             "internetarchive": "Internet Archive", "librivox": "LibriVox"}

# Gutenberg subject/bookshelf substring → our genre (utils.files.GENRES). Ordered:
# more-specific phrases first so "science fiction" wins over "science".
_SUBJECT_MAP = [
    ("science fiction", "Sci-Fi"), ("fantasy", "Fantasy"),
    ("detective and mystery", "Mystery"), ("mystery", "Mystery"),
    ("horror", "Horror"), ("ghost stories", "Horror"),
    ("love stories", "Romance"), ("romance", "Romance"),
    ("poetry", "Poetry"), ("drama", "Fiction"),
    ("autobiograph", "Biography"), ("biography", "Biography"),
    ("history", "History"), ("philosophy", "Non-Fiction"),
    ("self-help", "Self-Help"), ("conduct of life", "Self-Help"),
    ("business", "Business"), ("economic", "Business"),
    ("juvenile", "Children"), ("children", "Children"), ("fairy tales", "Children"),
    ("science", "Science"), ("natural history", "Science"),
    ("thriller", "Thriller"), ("adventure", "Fiction"),
    ("humor", "Fiction"), ("short stories", "Fiction"), ("fiction", "Fiction"),
]

# per-source in-process buffers of NORMALIZED candidates (single loop owner)
_buffers: dict[str, list[dict]] = {}
_MAX_SCAN_PER_TICK = 250   # cap dedupe-skips per source per tick so a tick stays light

# ── on-demand preemption ──────────────────────────────────────────────────────────
# A user request that searches the public archives PAUSES the background harvest so
# the user's title is sourced FIRST. _harvest_gate is set while the background loop
# may run; on-demand work clears it (ref-counted) and the loop waits on it.
_harvest_gate = asyncio.Event()
_harvest_gate.set()
_pause_count = 0


def _pause_background() -> None:
    global _pause_count
    _pause_count += 1
    _harvest_gate.clear()


def _resume_background() -> None:
    global _pause_count
    _pause_count = max(0, _pause_count - 1)
    if _pause_count == 0:
        _harvest_gate.set()


@contextlib.asynccontextmanager
async def on_demand():
    """Pause the background harvester for the duration of an on-demand request, so a
    user's live search / fetch runs before the steady-state back-fill."""
    _pause_background()
    try:
        yield
    finally:
        _resume_background()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _week() -> str:
    return _now().strftime("%G-W%V")   # ISO year-week


# ── kv helpers ───────────────────────────────────────────────────────────────────
async def _kv(key: str, default=None):
    db = await MongoManager.get()
    return await db.kv_get(key, default)


async def _kv_set(key: str, value) -> None:
    db = await MongoManager.get()
    await db.kv_set(key, value)


async def enabled() -> bool:
    return bool(await _kv("harvest_enabled", True))


async def set_enabled(on: bool) -> None:
    await _kv_set("harvest_enabled", bool(on))


# ── weekly cap / counters ─────────────────────────────────────────────────────────
async def week_count() -> int:
    if await _kv("harvest_week") != _week():
        return 0
    return int(await _kv("harvest_week_count", 0) or 0)


async def total_count() -> int:
    return int(await _kv("harvest_total", 0) or 0)


async def _bump_counters() -> None:
    """Record one successful add. Single-writer loop → plain read-modify is safe."""
    if await _kv("harvest_week") != _week():
        await _kv_set("harvest_week", _week())
        await _kv_set("harvest_week_count", 0)
    await _kv_set("harvest_week_count", int(await _kv("harvest_week_count", 0) or 0) + 1)
    await _kv_set("harvest_total", int(await _kv("harvest_total", 0) or 0) + 1)


async def _record_added(title: str) -> None:
    samples = await _kv("harvest_report_samples", []) or []
    samples.append(title[:60])
    await _kv_set("harvest_report_samples", samples[-25:])
    await _kv_set("harvest_report_added", int(await _kv("harvest_report_added", 0) or 0) + 1)


async def _record_fail() -> None:
    await _kv_set("harvest_report_fail", int(await _kv("harvest_report_fail", 0) or 0) + 1)


def fmt_cap(cap: int) -> str:
    """Human label for the weekly cap (0 / negative = no limit)."""
    return "∞" if cap <= 0 else str(cap)


async def status() -> dict:
    cap = int(await get_float("harvest_weekly_cap"))
    return {
        "enabled": await enabled(),
        "week_count": await week_count(),
        "cap": cap,
        "total": await total_count(),
        "sources": await _sources(),
        "page": int(await _kv("harvest_page_gutenberg", 1) or 1),
        "last_report": await _kv("harvest_last_report"),
        "added_since_report": int(await _kv("harvest_report_added", 0) or 0),
    }


# ── one-time cleanup of legacy non-allowed harvests ────────────────────────────────
async def _purge_nonallowed_once() -> None:
    """Drop index rows the harvester ITSELF created in a now-disallowed format
    (legacy txt/mobi from before the pdf/epub/audiobooks-only policy), so they're no
    longer searched or delivered. Scoped to harvester `src` values, so the operator's
    curated archive (no `src` field) is never touched; the channel messages remain.
    Guarded by a kv flag → runs exactly once (retries if a cluster errors)."""
    db = await MongoManager.get()
    if await db.kv_get("harvest_purged_nonallowed", False):
        return
    flt = {"src": {"$in": list(_SRC_NAME.keys())}, "ext": {"$nin": list(_ALLOWED_EXT)}}
    removed = 0
    for idx in db.healthy:
        try:
            res = await db.dbs[idx]["files"].delete_many(flt)
            removed += (res.deleted_count or 0)
        except Exception as exc:  # noqa: BLE001 — leave the flag unset → retry next boot
            logger.warning("harvest purge on cluster %s failed: %s", idx, exc)
            return
    await db.kv_set("harvest_purged_nonallowed", True)
    if removed:
        logger.info("Harvester purged %d legacy non-pdf/epub/audio rows.", removed)


# ── dedupe ───────────────────────────────────────────────────────────────────────
async def _seen(src: str, src_id: str, name_lc: str) -> bool:
    db = await MongoManager.get()
    if await db.find_one_global("files", {"src": src, "src_id": src_id}, {"_id": 1}):
        return True
    if name_lc and await db.find_one_global("files", {"name_lc": name_lc}, {"_id": 1}):
        return True
    return False


# ── Gutendex source ───────────────────────────────────────────────────────────────
async def _fetch_page(page: int) -> list[dict]:
    url = f"{GUTENDEX}?sort=descending&page={page}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                         headers=_UA) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return []
                data = await r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gutendex fetch page %d failed: %s", page, exc)
        return []
    return data.get("results") or []


def _pick_format(formats: dict) -> tuple[str | None, str | None]:
    for mime, ext in _FORMATS:
        for k, v in formats.items():
            if (isinstance(k, str) and k.startswith(mime) and isinstance(v, str)
                    and v.startswith("http") and not v.endswith(".zip")):
                return v, ext
    return None, None


def _normalize(raw: dict, langs: set[str]) -> dict | None:
    bid = raw.get("id")
    if bid is None:
        return None
    if langs and not (set(raw.get("languages") or []) & langs):
        return None
    title = clean_title(raw.get("title") or "")
    if len(title) < 2:
        return None
    url, ext = _pick_format(raw.get("formats") or {})
    if not url:
        return None
    authors = raw.get("authors") or []
    author = authors[0].get("name", "") if authors else ""
    return {
        "src": "gutenberg", "src_id": str(bid),
        "title": title, "name_lc": title.lower(), "author": author,
        "url": url, "ext": ext,
        "subjects": raw.get("subjects") or [], "bookshelves": raw.get("bookshelves") or [],
    }


# ── Standard Ebooks source (atom new-releases feed) ────────────────────────────────
async def _get_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                         headers=_UA) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("harvest GET(bytes) %s failed: %s", url, exc)
        return None


async def _get_json(url: str) -> dict | None:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40),
                                         headers=_UA) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None
                # IA/LibriVox sometimes serve JSON as text/plain → don't trust the
                # content-type, parse the body directly.
                return await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("harvest GET(json) %s failed: %s", url, exc)
        return None


def _int_size(raw) -> int:
    """Parse an archive.org file 'size' (a string of bytes) → int, 0 if unknown."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _section_key(name: str) -> str:
    """Collapse a LibriVox audio filename to its chapter identity, ignoring the
    bitrate variant — '..._01_twain_64kb.mp3' and '..._01_twain_128kb.mp3' are the
    same chapter, so they don't become two separate harvested files."""
    base = name.rsplit("/", 1)[-1]
    base = re.sub(r"\.[a-z0-9]+$", "", base, flags=re.I)        # drop extension
    base = re.sub(r"[_-]?\d{1,4}kb(ps)?$", "", base, flags=re.I)  # drop bitrate tag
    return base.lower()


def _section_seq(name: str) -> int:
    """First number in the filename → chapter order (so Part 2 sorts before 10)."""
    m = re.search(r"(\d{1,4})", name.rsplit("/", 1)[-1])
    return int(m.group(1)) if m else 0


async def _fetch_standardebooks() -> list[dict]:
    # Parse BYTES (not str): ElementTree rejects a decoded str that still carries an
    # XML encoding declaration ("Unicode strings with encoding declaration ...").
    raw = await _get_bytes(SE_FEED)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out: list[dict] = []
    for e in root.findall("a:entry", ns):
        title = clean_title(e.findtext("a:title", default="", namespaces=ns) or "")
        if len(title) < 2:
            continue
        eid = (e.findtext("a:id", default="", namespaces=ns) or "").strip()
        ae = e.find("a:author/a:name", ns)
        author = (ae.text or "").strip() if ae is not None else ""
        url = None
        for link in e.findall("a:link", ns):
            if "epub+zip" in (link.get("type") or "") and link.get("href"):
                url = link.get("href")
                break
        if not url:
            continue
        if url.startswith("/"):
            url = "https://standardebooks.org" + url
        if not url.startswith("http"):
            continue
        subjects = [c.get("term") for c in e.findall("a:category", ns) if c.get("term")]
        out.append({"src": "standardebooks", "src_id": eid or url, "title": title,
                    "name_lc": title.lower(), "author": author, "url": url, "ext": "epub",
                    "subjects": subjects, "bookshelves": []})
    return out


# ── Internet Archive source (EPUB + PDF, public-domain only) ───────────────────────
def _ia_lang_clause(langs: set[str]) -> str:
    # IA stores language as "eng"/"English"; only constrain when the operator wants
    # English (the default) — otherwise pull every language.
    return " AND language:(eng OR English)" if "en" in langs else ""


def _first_str(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v else ""


async def _fetch_ia(page: int, langs: set[str]) -> list[dict]:
    q = ("mediatype:texts AND possible-copyright-status:NOT_IN_COPYRIGHT "
         "AND format:(EPUB OR PDF)" + _ia_lang_clause(langs))
    url = (f"{IA_SEARCH}?q={quote(q)}&fl[]=identifier&fl[]=title&fl[]=creator"
           f"&sort[]=addeddate+desc&rows={IA_ROWS}&page={page}&output=json")
    data = await _get_json(url)
    docs = ((data or {}).get("response") or {}).get("docs") or []
    out: list[dict] = []
    for d in docs:
        ident = d.get("identifier")
        if not ident:
            continue
        title = clean_title(_first_str(d.get("title")))
        if len(title) < 2:
            continue
        out.append({
            "src": "internetarchive", "src_id": str(ident), "ia_id": str(ident),
            "title": title, "name_lc": title.lower(),
            "author": _first_str(d.get("creator")),
            "subjects": [], "bookshelves": [], "resolve": "ia"})
    return out


async def _resolve_ia(cand: dict, max_bytes: int) -> bool:
    """Fill cand['url']/['ext'] with the item's EPUB (preferred) or PDF under the
    size cap. Returns False if neither exists small enough."""
    md = await _get_json(IA_META + cand["ia_id"])
    if not md:
        return False
    files = md.get("files") or []
    for want in ("epub", "pdf"):
        best = None
        for f in files:
            if not isinstance(f, dict):
                continue
            nm = f.get("name") or ""
            if _ext_of(nm) != want:
                continue
            size = _int_size(f.get("size"))
            if size and size > max_bytes:
                continue
            rank = size or (1 << 62)            # prefer the smallest that fits
            if best is None or rank < best[1]:
                best = (nm, rank)
        if best:
            cand["url"] = IA_DOWNLOAD + cand["ia_id"] + "/" + quote(best[0])
            cand["ext"] = want
            subj = (md.get("metadata") or {}).get("subject")
            if isinstance(subj, list):
                cand["subjects"] = subj
            elif subj:
                cand["subjects"] = [str(subj)]
            return True
    return False


# ── LibriVox source (audiobooks, one harvested file per chapter) ───────────────────
async def _fetch_librivox(offset: int) -> list[dict]:
    url = f"{LV_FEED}?format=json&limit={LV_ROWS}&offset={offset}"
    data = await _get_json(url)
    books = (data or {}).get("books") or []
    out: list[dict] = []
    for b in books:
        bid = b.get("id")
        title = clean_title(b.get("title") or "")
        if bid is None or len(title) < 2:
            continue
        # LibriVox has no url_iarchive field — the archive.org identifier lives
        # inside url_zip_file: .../compress/<IDENTIFIER>/formats=...
        m = re.search(r"/compress/([^/]+)/", b.get("url_zip_file") or "")
        if not m:
            continue
        authors = b.get("authors") or []
        a0 = authors[0] if authors else {}
        author = " ".join(x for x in [a0.get("first_name"), a0.get("last_name")] if x).strip()
        genres = b.get("genres") or []
        out.append({
            "src": "librivox", "src_id": str(bid), "ia_id": m.group(1),
            "title": title, "name_lc": title.lower(), "author": author,
            "subjects": [g.get("name") for g in genres if isinstance(g, dict) and g.get("name")],
            "bookshelves": [], "lv_book": True})
    return out


# Audio layout on a LibriVox archive item: ONE whole-book container (.m4b, usually
# 100s of MB) PLUS per-chapter .mp3 AND .ogg variants. We keep mp3 over ogg for
# chapters (ogg won't play in some Telegram webviews), and only ever take the m4b
# when the whole book fits under the cap (else it's far too big to send).
_WHOLE_EXT = {"m4b", "m4a"}
_CHAPTER_PREF = {"mp3": 0, "aac": 1, "ogg": 2, "opus": 3}   # lower = preferred


def _lv_candidate(book: dict, name: str, ext: str, src_id: str, title: str) -> dict:
    return {
        "src": "librivox", "src_id": src_id, "title": title,
        "name_lc": title.lower(), "author": book.get("author", ""),
        "url": IA_DOWNLOAD + book["ia_id"] + "/" + quote(name), "ext": ext,
        "subjects": book.get("subjects", []), "bookshelves": [],
        # audio: dedupe on (src, src_id) ONLY — an audiobook is a different medium
        # from an ebook of the same title, so it must not collide with one on name_lc.
        "audio": True}


async def _expand_librivox(book: dict, max_bytes: int) -> list[dict]:
    """Turn one LibriVox book into audio candidates that each fit under the cap:
    a single whole-book file when it fits, otherwise one file per chapter
    ("<Title> — Part NN"). Never mixes a whole-book file with its chapters."""
    md = await _get_json(IA_META + book["ia_id"])
    if not md:
        return []
    files = [f for f in (md.get("files") or []) if isinstance(f, dict) and f.get("name")]

    # 1) Whole-book container (.m4b) that fits → the cleanest single deliverable.
    wholes = []
    for f in files:
        if _ext_of(f["name"]) in _WHOLE_EXT:
            size = _int_size(f.get("size"))
            if size and size <= max_bytes:          # must KNOW it fits
                wholes.append((size, f["name"], _ext_of(f["name"])))
    if wholes:
        _sz, nm, ext = min(wholes)
        return [_lv_candidate(book, nm, ext, f"{book['src_id']}:full", book["title"])]

    # 2) Otherwise per-chapter, mp3 preferred, smallest variant of each chapter ≤ cap.
    sections: dict[str, tuple] = {}   # chapter key → (pref, size, name, ext, seq)
    for f in files:
        ext = _ext_of(f["name"])
        if ext not in _CHAPTER_PREF:
            continue
        size = _int_size(f.get("size"))
        if size and size > max_bytes:
            continue
        key = _section_key(f["name"])
        rank = (_CHAPTER_PREF[ext], size or (1 << 62))
        cur = sections.get(key)
        if cur is None or rank < (cur[0], cur[1]):
            sections[key] = (rank[0], rank[1], f["name"], ext, _section_seq(f["name"]))
    ordered = sorted(sections.values(), key=lambda x: (x[4], x[2]))
    multi = len(ordered) > 1
    out: list[dict] = []
    for n, (_p, _sz, nm, ext, _seq) in enumerate(ordered, start=1):
        title = f"{book['title']} — Part {n:02d}" if multi else book["title"]
        out.append(_lv_candidate(book, nm, ext, f"{book['src_id']}:{n:02d}", title))
    return out


# ── multi-source candidate selection ───────────────────────────────────────────────
async def _sources() -> list[str]:
    raw = await _kv("harvest_sources", "standardebooks,gutenberg,internetarchive,librivox") or ""
    return [s.strip() for s in raw.split(",") if s.strip()] or ["gutenberg"]


async def _refill(src: str, langs: set[str]) -> bool:
    """Fill a source's candidate buffer. Returns whether anything was added."""
    if src == "gutenberg":
        page = int(await _kv("harvest_page_gutenberg", 1) or 1)
        results = await _fetch_page(page)
        if not results:
            await _kv_set("harvest_page_gutenberg", 1)   # end of catalog → wrap
            return False
        await _kv_set("harvest_page_gutenberg", page + 1)
        _buffers[src] = [c for c in (_normalize(r, langs) for r in results) if c]
        return bool(_buffers[src])
    if src == "standardebooks":
        # the new-releases feed is small and rarely changes — poll at most hourly
        last = _parse_iso(await _kv("harvest_se_last_fetch"))
        if last and (_now() - last) < timedelta(seconds=_SE_REFETCH_SEC):
            return False
        await _kv_set("harvest_se_last_fetch", _now().isoformat())
        _buffers[src] = await _fetch_standardebooks()
        return bool(_buffers[src])
    if src == "internetarchive":
        page = int(await _kv("harvest_page_internetarchive", 1) or 1)
        results = await _fetch_ia(page, langs)
        if not results:
            await _kv_set("harvest_page_internetarchive", 1)   # end of run → wrap
            return False
        await _kv_set("harvest_page_internetarchive", page + 1)
        _buffers[src] = results
        return bool(_buffers[src])
    if src == "librivox":
        offset = int(await _kv("harvest_offset_librivox", 0) or 0)
        results = await _fetch_librivox(offset)
        if not results:
            await _kv_set("harvest_offset_librivox", 0)        # end of catalog → wrap
            return False
        await _kv_set("harvest_offset_librivox", offset + len(results))
        _buffers[src] = results
        return bool(_buffers[src])
    return False


async def _candidate_from(src: str, langs: set[str]) -> dict | None:
    buf = _buffers.setdefault(src, [])
    max_bytes = int(max(1.0, await get_float("harvest_max_mb")) * 1024 * 1024)
    scanned = 0
    fetches = 0          # bound per-tick source-metadata fetches (IA/LibriVox)
    while scanned < _MAX_SCAN_PER_TICK:
        if not buf:
            if not await _refill(src, langs):
                return None
            buf = _buffers.get(src, [])
            if not buf:
                return None
        cand = buf.pop(0)
        scanned += 1
        # A LibriVox 'book' fans out into per-chapter candidates (one metadata fetch).
        if cand.get("lv_book"):
            if fetches >= _RESOLVE_BUDGET:
                buf.insert(0, cand)
                return None
            fetches += 1
            sections = await _expand_librivox(cand, max_bytes)
            # Re-queue ONLY the chapters/files not yet indexed — so a book whose
            # in-memory queue was lost to a restart (after the offset cursor moved
            # past it) resumes and fills its missing chapters instead of being
            # skipped forever. A fully-harvested book yields nothing and is dropped.
            pending = [s for s in sections
                       if not await _seen(s["src"], s["src_id"], None)]
            if pending:
                buf[0:0] = pending      # process this book's missing chapters next
            continue
        # Audio dedupes on (src, src_id) only; ebooks also dedupe on normalized title.
        if await _seen(cand["src"], cand["src_id"],
                       None if cand.get("audio") else cand["name_lc"]):
            continue
        # Internet Archive candidates need a metadata fetch to resolve a download URL.
        if not cand.get("url"):
            if fetches >= _RESOLVE_BUDGET:
                buf.insert(0, cand)
                return None
            fetches += 1
            if cand.get("resolve") == "ia":
                if not await _resolve_ia(cand, max_bytes):
                    continue
            else:
                continue
        if cand.get("ext") not in _ALLOWED_EXT:
            continue     # belt-and-suspenders: only pdf / epub / audiobooks ingested
        return cand
    return None


async def _next_candidate() -> dict | None:
    """The next un-indexed public-domain title across all enabled sources. Sources
    are visited round-robin (a rotating start each tick) so EPUB (Gutenberg/Standard
    Ebooks/IA), PDF (Internet Archive) and audiobooks (LibriVox) all flow instead of
    one source starving the others. Bounded per-source scan keeps each tick light."""
    langs = {x.strip() for x in (await _kv("harvest_langs", "en") or "en").split(",") if x.strip()}
    srcs = await _sources()
    if not srcs:
        return None
    start = int(await _kv("harvest_rr", 0) or 0) % len(srcs)
    await _kv_set("harvest_rr", (start + 1) % len(srcs))
    order = srcs[start:] + srcs[:start]
    for src in order:
        cand = await _candidate_from(src, langs)
        if cand:
            return cand
    return None


# ── download + ingest ──────────────────────────────────────────────────────────────
async def _download(url: str, max_bytes: int) -> bytes | None:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120),
                                         headers=_UA) as s:
            async with s.get(url, allow_redirects=True) as r:
                if r.status != 200:
                    return None
                cl = r.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > max_bytes:
                    return None
                buf = bytearray()
                async for chunk in r.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return None
                return bytes(buf)
    except Exception as exc:  # noqa: BLE001
        logger.warning("harvest download failed (%s): %s", url, exc)
        return None


def _safe_filename(title: str, ext: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", title).strip()[:120] or "book"
    return f"{base}.{ext}"


async def _resolve_genre(cand: dict) -> str | None:
    blob = " ".join(cand.get("subjects", []) + cand.get("bookshelves", [])).lower()
    for key, genre in _SUBJECT_MAP:
        if key in blob:
            return genre
    # fall back to the AI engine only when the free subject tags don't resolve
    if bool(await _kv("harvest_ai_genre", True)):
        try:
            from utils.ai import classify_genre
            return await classify_genre(cand["title"])
        except Exception:  # noqa: BLE001
            return None
    return None


async def _ingest(cand: dict, bot) -> bool:
    chan = await get_file_channel()
    if not chan:
        return False
    # Hard format gate: never ingest anything but pdf / epub / audiobooks.
    if not cand.get("url") or cand.get("ext") not in _ALLOWED_EXT:
        return False
    max_bytes = int(max(1.0, await get_float("harvest_max_mb")) * 1024 * 1024)
    data = await _download(cand["url"], max_bytes)
    if not data or len(data) < 1024:   # too small → almost certainly an error page
        await _record_fail()
        return False

    fname = _safe_filename(cand["title"], cand["ext"])
    cap_author = f"\n✍️ {escape(cand['author'])}" if cand.get("author") else ""
    src_name = _SRC_NAME.get(cand["src"], "a public-domain archive")
    caption = (f"📚 <b>{escape(cand['title'])}</b>{cap_author}\n"
               f"<i>Added from the public-domain archive ({src_name}).</i>")
    # Bake the admin cover thumbnail on at ingest (the harvester re-uploads anyway),
    # so harvested files arrive fully branded with no later prep pass.
    from utils import prepare
    thumb = await prepare._thumb_bytes(bot)
    try:
        sent = await bot.send_document(
            chan, BufferedInputFile(data, filename=fname),
            thumbnail=(BufferedInputFile(thumb, filename="cover.jpg") if thumb else None),
            caption=caption, disable_notification=True)
    except Exception as exc:  # noqa: BLE001 — channel perms / size / rate
        logger.warning("harvest upload failed for %s: %s", cand["title"], exc)
        await _record_fail()
        return False

    doc = extract_from_message(sent)
    if not doc:
        return False
    genre = await _resolve_genre(cand)
    doc.update({"name": cand["title"], "name_lc": cand["name_lc"],
                "src": cand["src"], "src_id": cand["src_id"]})
    if cand.get("author"):
        doc["author"] = cand["author"]
    if genre:
        doc["genre"] = genre
    await index_file(doc)   # inserts if new

    # Enrich the row whether index_file inserted it OR the channel_post indexer
    # (which also sees the bot's own post) inserted a bare version first — keyed on
    # the channel message so src/genre/clean-name always land. Race-safe.
    db = await MongoManager.get()
    enrich = {"src": cand["src"], "src_id": cand["src_id"],
              "name": cand["title"], "name_lc": cand["name_lc"],
              "name_tg": trigrams(cand["name_lc"]),
              # harvested titles are already clean, and this upload IS the branded
              # copy (cover baked on above) — so mark it prepared to skip later prep.
              "clean_name": cand["title"],
              "prepared_msg_id": doc.get("msg_id"),
              "prepared_file_id": doc.get("file_id"),
              "brand_state": "prepared" if thumb else "caption"}
    if genre:
        enrich["genre"] = genre
    if cand.get("author"):
        enrich["author"] = cand["author"]
    await db.safe_update("files", {"chan_id": doc.get("chan_id"), "msg_id": doc.get("msg_id")},
                         {"$set": enrich}, upsert=False)

    await _bump_counters()
    await _record_added(cand["title"])
    # auto-notify anyone who was watching for this title
    try:
        from handlers.indexer import _service_watchlist
        await _service_watchlist(bot, cand["title"])
    except Exception:  # noqa: BLE001
        pass
    logger.info("Harvested: %s (%s)", cand["title"], cand["ext"])
    # if this was a numbered series volume, queue the NEXT volume to chase next.
    try:
        await _queue_next_volume(cand)
    except Exception:  # noqa: BLE001 — never let series logic break an ingest
        pass
    return True


# ── weekly admin digest (7-day timer) ──────────────────────────────────────────────
def _parse_iso(s) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _admin_ids() -> set[int]:
    out = set()
    try:
        out |= {int(a) for a in (ADMIN_IDS or [])}
    except (TypeError, ValueError):
        pass
    if SUPER_ADMIN_ID:
        out.add(int(SUPER_ADMIN_ID))
    return {a for a in out if a}


async def _send_report(bot) -> None:
    added = int(await _kv("harvest_report_added", 0) or 0)
    failed = int(await _kv("harvest_report_fail", 0) or 0)
    samples = await _kv("harvest_report_samples", []) or []
    total = await total_count()
    cap = int(await get_float("harvest_weekly_cap"))
    sample_lines = "\n".join(f"• {escape(s)}" for s in samples[-10:]) or "—"
    body = (
        "📚 <b>Weekly Archive Harvest</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your library grew on autopilot this week.</i>\n"
        "<blockquote>"
        f"✅ <b>Added this period:</b> <code>{added}</code>\n"
        f"📦 <b>Archive total harvested:</b> <code>{total}</code>\n"
        f"🎯 <b>Weekly cap:</b> <code>{fmt_cap(cap)}</code>\n"
        f"⚠️ <b>Failed/skipped fetches:</b> <code>{failed}</code>"
        "</blockquote>\n"
        "<b>Newest titles</b>\n"
        f"<blockquote expandable>{sample_lines}</blockquote>\n"
        "<i>💡 Tune or pause this in Admin → 🧰 More Tools → 📚 Harvester.</i>")
    for uid in _admin_ids():
        try:
            await bot.send_message(uid, body)
        except Exception:  # noqa: BLE001
            pass
    # reset the rolling window
    await _kv_set("harvest_report_added", 0)
    await _kv_set("harvest_report_fail", 0)
    await _kv_set("harvest_report_samples", [])
    await _kv_set("harvest_last_report", _now().isoformat())


async def _maybe_report(bot, force: bool = False) -> bool:
    last = _parse_iso(await _kv("harvest_last_report"))
    if last is None:
        # first ever run: baseline the timer, don't fire immediately
        await _kv_set("harvest_last_report", _now().isoformat())
        return False
    if force or (_now() - last) >= timedelta(days=7):
        await _send_report(bot)
        return True
    return False


async def report_now(bot) -> None:
    """Admin-triggered: send the digest immediately and reset the window."""
    await _send_report(bot)


# ── on-demand public search + single-file ingest (request flow) ─────────────────────
def _norm_base(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def _search_gutenberg(title: str, langs: set[str]) -> list[dict]:
    data = await _get_json(f"{GUTENDEX}?search={quote(title)}")
    out = []
    for raw in ((data or {}).get("results") or [])[:16]:
        c = _normalize(raw, langs)
        if c:
            out.append(c)
    return out


async def _search_ia(title: str, langs: set[str], max_bytes: int) -> list[dict]:
    safe = re.sub(r'["()]', " ", title).strip()
    if not safe:
        return []
    q = (f'title:("{safe}") AND mediatype:texts AND '
         f'possible-copyright-status:NOT_IN_COPYRIGHT AND format:(EPUB OR PDF)'
         + _ia_lang_clause(langs))
    url = (f"{IA_SEARCH}?q={quote(q)}&fl[]=identifier&fl[]=title&fl[]=creator"
           f"&rows=8&page=1&output=json")
    data = await _get_json(url)
    docs = ((data or {}).get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        ident = d.get("identifier")
        if not ident:
            continue
        t = clean_title(_first_str(d.get("title")))
        if len(t) < 2:
            continue
        cand = {"src": "internetarchive", "src_id": str(ident), "ia_id": str(ident),
                "title": t, "name_lc": t.lower(), "author": _first_str(d.get("creator")),
                "subjects": [], "bookshelves": [], "resolve": "ia"}
        if await _resolve_ia(cand, max_bytes):
            out.append(cand)
        if len(out) >= 5:
            break
    return out


async def _search_librivox(title: str, max_bytes: int) -> list[dict]:
    data = await _get_json(f"{LV_FEED}?format=json&title={quote(title)}&limit=3")
    books = (data or {}).get("books") or []
    out: list[dict] = []
    for b in books:
        bid = b.get("id")
        t = clean_title(b.get("title") or "")
        if bid is None or len(t) < 2:
            continue
        m = re.search(r"/compress/([^/]+)/", b.get("url_zip_file") or "")
        if not m:
            continue
        authors = b.get("authors") or []
        a0 = authors[0] if authors else {}
        author = " ".join(x for x in [a0.get("first_name"), a0.get("last_name")] if x).strip()
        genres = b.get("genres") or []
        book = {"src": "librivox", "src_id": str(bid), "ia_id": m.group(1),
                "title": t, "name_lc": t.lower(), "author": author,
                "subjects": [g.get("name") for g in genres if isinstance(g, dict) and g.get("name")],
                "bookshelves": [], "lv_book": True}
        sections = await _expand_librivox(book, max_bytes)
        out.extend(sections[:6])
        if out:
            break   # one matching audiobook is plenty for a pick list
    return out


async def search_public(title: str, langs: set[str] | None = None, *, limit: int = 8) -> list[dict]:
    """Live title search across the public-domain sources (Project Gutenberg,
    Internet Archive, LibriVox). Returns RESOLVED, ready-to-ingest candidates (each
    carries a url + ext), deduped by normalized title and excluding anything already
    in the archive. Used when a user requests a title we don't have yet."""
    title = (title or "").strip()
    if len(title) < 2:
        return []
    if langs is None:
        langs = {x.strip() for x in (await _kv("harvest_langs", "en") or "en").split(",") if x.strip()}
    max_bytes = int(max(1.0, await get_float("harvest_max_mb")) * 1024 * 1024)
    groups = await asyncio.gather(
        _search_gutenberg(title, langs),
        _search_ia(title, langs, max_bytes),
        _search_librivox(title, max_bytes),
        return_exceptions=True)
    out, seen = [], set()
    for g in groups:
        if not isinstance(g, list):
            continue
        for c in g:
            key = c.get("name_lc") or ""
            if (not key or key in seen or c.get("ext") not in _ALLOWED_EXT
                    or not c.get("url")):
                continue
            if await _seen(c["src"], c["src_id"], c["name_lc"]):  # already archived
                continue
            seen.add(key)
            out.append(c)
            if len(out) >= limit:
                return out
    return out


async def ingest_one(cand: dict, bot) -> str | None:
    """Download + index ONE public candidate and return the indexed file's
    file_unique_id (so the caller can deliver it), or None on failure."""
    if not cand.get("url") and cand.get("resolve") == "ia":
        max_bytes = int(max(1.0, await get_float("harvest_max_mb")) * 1024 * 1024)
        if not await _resolve_ia(cand, max_bytes):
            return None
    if not cand.get("url") or cand.get("ext") not in _ALLOWED_EXT:
        return None
    if not await _ingest(cand, bot):
        return None
    db = await MongoManager.get()
    doc = await db.find_one_global(
        "files", {"src": cand["src"], "src_id": cand["src_id"]}, {"file_unique_id": 1})
    return doc.get("file_unique_id") if doc else None


# ── series completion (auto-pull the next volume once a part lands) ──────────────────
async def _queue_next_volume(cand: dict) -> None:
    """After ingesting a series volume N, remember to chase volume N+1 next."""
    from utils.series import parse_series
    parsed = parse_series(cand.get("title", ""))
    if not parsed:
        return
    base, vol = parsed
    if not base or vol >= 99:
        return
    q = await _kv("harvest_series_queue", []) or []
    nb = _norm_base(base)
    if any(_norm_base(e.get("base", "")) == nb and int(e.get("vol") or 0) == vol + 1 for e in q):
        return
    q.append({"base": base, "vol": vol + 1})
    await _kv_set("harvest_series_queue", q[-100:])


async def _next_series_candidate() -> dict | None:
    """The next series volume to chase: once Part 1 lands, pull Part 2, 3, … until
    the collection is complete or a volume can't be found (then the chain stops)."""
    from utils.series import parse_series
    q = await _kv("harvest_series_queue", []) or []
    if not q:
        return None
    tried = 0
    found = None
    while q and tried < 3 and found is None:
        entry = q.pop(0)
        tried += 1
        base, vol = str(entry.get("base") or ""), int(entry.get("vol") or 0)
        if not base or vol < 1:
            continue
        nb = _norm_base(base)
        for c in await search_public(f"{base} {vol}", limit=6):
            p = parse_series(c.get("title", ""))
            if p and p[1] == vol and _norm_base(p[0]) == nb:
                found = c
                break
    await _kv_set("harvest_series_queue", q)
    return found


# ── background loop ────────────────────────────────────────────────────────────────
async def run_harvest_loop(bot) -> None:
    logger.info("Archive harvester started.")
    if not await _kv("harvest_last_report"):
        await _kv_set("harvest_last_report", _now().isoformat())
    # small startup delay so it never competes with boot work
    await asyncio.sleep(30)
    try:
        await _purge_nonallowed_once()   # retire legacy txt/mobi harvests (one-time)
    except Exception as exc:  # noqa: BLE001 — cleanup must never block the loop
        logger.warning("harvest one-time purge skipped: %s", exc)
    while True:
        try:
            interval = max(15.0, await get_float("harvest_interval_sec"))
            await asyncio.sleep(interval)
            await _harvest_gate.wait()     # yield while an on-demand request is sourcing
            await _maybe_report(bot)
            if not await enabled():
                continue
            if not await get_file_channel():
                continue   # no channel to upload into → idle
            cap = int(await get_float("harvest_weekly_cap"))
            if cap > 0 and await week_count() >= cap:
                continue   # optional throttle reached → idle until next ISO week
                           # (cap 0 = unlimited, the default)
            # series completion takes priority over the steady-state back-fill, so a
            # part-1 we just added pulls its sequels before anything else.
            cand = await _next_series_candidate() or await _next_candidate()
            if not cand:
                await asyncio.sleep(1800)   # caught up / API down → idle ~30 min
                continue
            await _ingest(cand, bot)
        except asyncio.CancelledError:
            logger.info("Harvester stopped.")
            break
        except Exception as exc:  # noqa: BLE001 — must never take the bot down
            logger.error("Harvester loop error: %s", exc, exc_info=True)
            await asyncio.sleep(60)
