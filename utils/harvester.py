"""
utils/harvester.py — automated public-domain book harvester.

A light background worker that grows the archive on its own. It pulls *latest
arrivals* and back-fills books not yet in our database from PUBLIC-DOMAIN sources
(Project Gutenberg via the Gutendex JSON API, and Standard Ebooks via its OPDS
new-releases feed — both legal to redistribute), uploads
each file to the bot's database (file) channel, and indexes it so the normal
search → `dl:` delivery + watchlist all work unchanged. Users searching a title
get it automatically once it lands; anyone on the watchlist for it is pinged.

Design goals:
  • LIGHT — one file per tick, paced (harvest_interval_sec, ~75s default), hard
    weekly cap (harvest_weekly_cap, 10k). Idles long when caught up.
  • SAFE — public-domain only; size-capped downloads; never crashes the bot.
  • SMART — dedupes against the existing archive by source id AND normalized
    title (so we never duplicate the 30k legacy files); genre comes free from
    Gutenberg's own subject tags, falling back to the AI engine only when unclear.
  • QUIET — fully background; admins get ONE weekly digest (7-day timer), not
    per-file spam.

All cursors/counters live in Mongo kv (single-writer loop → plain read-modify is
safe). Requires the file channel to be set (Admin → 🗂 File Channel) and the bot
to be an admin of it; otherwise it simply idles.
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html import escape

import aiohttp
from aiogram.types import BufferedInputFile

from config import ADMIN_IDS, SUPER_ADMIN_ID
from database.connection import MongoManager
from utils.channel import get_file_channel
from utils.files import clean_title, extract_from_message, index_file, trigrams
from utils.settings import get_float

logger = logging.getLogger(__name__)

GUTENDEX = "https://gutendex.com/books/"
# Standard Ebooks' OPDS feed is now Patron-gated (401); the atom new-releases feed
# is public and carries direct .epub download links.
SE_FEED = "https://standardebooks.org/feeds/atom/new-releases"
_SE_REFETCH_SEC = 3600   # Standard Ebooks new-releases feed is tiny; poll hourly
_UA = {"User-Agent": "BooksBot/1.0 (public-domain archive harvester)"}

# Download-format preference (mime prefix → extension). epub reads best in the
# Mini-App reader; txt is the universal fallback. We avoid html and opaque
# octet-stream/zip bundles.
_FORMATS = [
    ("application/epub+zip", "epub"),
    ("application/x-mobipocket-ebook", "mobi"),
    ("text/plain", "txt"),
]

_SRC_NAME = {"gutenberg": "Project Gutenberg", "standardebooks": "Standard Ebooks"}

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


# ── multi-source candidate selection ───────────────────────────────────────────────
async def _sources() -> list[str]:
    raw = await _kv("harvest_sources", "standardebooks,gutenberg") or ""
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
    return False


async def _candidate_from(src: str, langs: set[str]) -> dict | None:
    buf = _buffers.setdefault(src, [])
    scanned = 0
    while scanned < _MAX_SCAN_PER_TICK:
        if not buf:
            if not await _refill(src, langs):
                return None
            buf = _buffers.get(src, [])
            if not buf:
                return None
        cand = buf.pop(0)
        scanned += 1
        if await _seen(cand["src"], cand["src_id"], cand["name_lc"]):
            continue
        return cand
    return None


async def _next_candidate() -> dict | None:
    """The next un-indexed public-domain book across all enabled sources, newest
    first (Standard Ebooks new releases checked before the Gutenberg backfill).
    Bounded per-source dedupe-scan keeps each tick light."""
    langs = {x.strip() for x in (await _kv("harvest_langs", "en") or "en").split(",") if x.strip()}
    for src in await _sources():
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
        f"🎯 <b>Weekly cap:</b> <code>{cap}</code>\n"
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


# ── background loop ────────────────────────────────────────────────────────────────
async def run_harvest_loop(bot) -> None:
    logger.info("Archive harvester started.")
    if not await _kv("harvest_last_report"):
        await _kv_set("harvest_last_report", _now().isoformat())
    # small startup delay so it never competes with boot work
    await asyncio.sleep(30)
    while True:
        try:
            interval = max(15.0, await get_float("harvest_interval_sec"))
            await asyncio.sleep(interval)
            await _maybe_report(bot)
            if not await enabled():
                continue
            if not await get_file_channel():
                continue   # no channel to upload into → idle
            cap = int(await get_float("harvest_weekly_cap"))
            if await week_count() >= cap:
                continue   # weekly cap reached → idle until next ISO week
            cand = await _next_candidate()
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
