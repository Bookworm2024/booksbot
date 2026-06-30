"""
utils/prepare.py — file preparation & branding (the "renamer" layer).

Every file the bot hands a user flows through here so the experience is consistent
and branded:

  • CLEAN NAME — messy archive filenames ("OceanofPDF_Atomic_Habits_",
    "ATOMIC_HABITS_!") become a clean human title ("Atomic Habits") via a fast
    regex cleaner refined by the AI engine. Computed once, cached on the files doc
    as `clean_name`, and used for button labels AND captions everywhere.
  • BRANDED CAPTION — delivered files are captioned "<b>Title</b>  @handle"
    (the handle is admin-set: kv `brand_handle`).
  • COVER THUMBNAIL — where Telegram allows it (a document the bot can re-upload:
    bot-usable bytes, ≤20MB — the Bot API download cap), the admin's branding image
    (kv `brand_thumb_file_id`) is baked on as the document thumbnail with a clean
    filename. Done ONCE per file (re-uploaded to the file channel, coords cached as
    `prepared_msg_id`/`prepared_file_id`); thereafter delivery is a cheap copy.
    Files we can't re-upload (Telethon legacy, >20MB, audio) gracefully fall back
    to clean-name + branded-caption only — never a failure.

A short "📤 Preparing your file…" message covers the one-time prep latency; cached
files deliver instantly.

Re-uploaded (prepared/staging) channel posts carry an invisible PREP_MARKER in
their caption so the live channel-post indexer skips them (they're duplicates of
an already-indexed file, not new archive entries).
"""
import asyncio
import logging
import re
from html import escape

from aiogram.types import BufferedInputFile

from database.connection import MongoManager
from utils import ai
from utils.channel import get_file_channel
from utils.files import icon_for

logger = logging.getLogger(__name__)

# Bot API can only download files ≤ 20 MB, so only those can be re-uploaded with a
# custom thumbnail. Larger files / non-bot-usable ids fall back to caption branding.
_DOWNLOAD_CAP = 20 * 1024 * 1024
_THUMB_MAX_PX = 320
_THUMB_MAX_BYTES = 200 * 1024

# Invisible marker (word-joiners) prepended to prepared/staging upload captions so
# handlers.indexer skips re-indexing them as new files.
PREP_MARKER = "⁠⁠"

_BOOK_EXT = {"pdf", "epub", "mobi", "azw3", "txt", "doc", "docx", "fb2", "cbz", "cbr", "rtf"}

# in-process cache of the processed thumbnail: {brand_thumb_file_id: jpeg_bytes}
_thumb_cache: dict[str, bytes] = {}


# ── kv brand settings ──────────────────────────────────────────────────────────────
async def _kv(key: str, default=None):
    db = await MongoManager.get()
    return await db.kv_get(key, default)


async def _kv_set(key: str, value) -> None:
    db = await MongoManager.get()
    await db.kv_set(key, value)


async def brand_enabled() -> bool:
    return bool(await _kv("brand_enabled", True))


async def thumb_enabled() -> bool:
    return bool(await _kv("brand_thumb_enabled", True))


async def handle() -> str:
    h = (await _kv("brand_handle", "@bookslibraryofficial") or "").strip()
    if h and not h.startswith("@") and not h.startswith("http"):
        h = "@" + h
    return h


async def thumb_file_id() -> str:
    return (await _kv("brand_thumb_file_id", "") or "").strip()


# ── name cleaning ───────────────────────────────────────────────────────────────────
_EXT_RE = re.compile(r"\.(pdf|epub|mobi|azw3|txt|cbz|cbr|zip|rar|doc|docx|fb2|rtf)$", re.I)
_HANDLE_RE = re.compile(r"@\w+")
_URL_RE = re.compile(r"(www\.\S+|https?://\S+)", re.I)
# site names, optionally swallowing a trailing TLD (z-lib.org)
_SITE_RE = re.compile(
    r"\b(oceanofpdf|ocean of pdf|z-?lib(?:rary)?|libgen|pdf ?drive|annas?[ _-]?archive|"
    r"epub ?pub|bookfi|planetebook|free ?books?|getbook|booksvooks)(?:\.[a-z]{2,4})?", re.I)
_LEADNUM_RE = re.compile(r"^\s*\d{5,}[\s_-]+")   # strip long numeric id prefixes
_TRAIL_FMT_RE = re.compile(r"[\s_-]+(pdf|epub|mobi|azw3|ebook)\s*$", re.I)  # bare trailing format word
_SEP_RE = re.compile(r"[_]+")
_MULTISPACE = re.compile(r"\s+")
_SMALL = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
          "at", "by", "from", "as", "but", "nor", "vs"}


def _smart_title(s: str) -> str:
    out = []
    for i, w in enumerate(s.split()):
        lw = w.lower()
        out.append(lw if (i > 0 and lw in _SMALL) else (lw[:1].upper() + lw[1:]))
    return " ".join(out)


def basic_clean(name: str, ext: str | None = None) -> str:
    """Fast, free regex cleaner — a decent title instantly (AI refines later)."""
    s = name or ""
    s = _EXT_RE.sub("", s)
    s = _SEP_RE.sub(" ", s)        # underscores → spaces FIRST so \b site matches work
    s = _HANDLE_RE.sub(" ", s)
    s = _URL_RE.sub(" ", s)
    s = _SITE_RE.sub(" ", s)
    s = _LEADNUM_RE.sub("", s)
    s = _TRAIL_FMT_RE.sub("", s)
    s = _MULTISPACE.sub(" ", s).strip(" -–—.!_")
    if not s:
        s = (name or "Book").strip(" -–—.!_") or "Book"
    if s.islower() or s.isupper():
        s = _smart_title(s)
    return s[:120]


async def _persist_clean(fuid: str, clean: str) -> None:
    if not fuid:
        return
    db = await MongoManager.get()
    await db.safe_update("files", {"file_unique_id": fuid},
                         {"$set": {"clean_name": clean}}, upsert=False)


async def ensure_clean(doc: dict) -> str:
    """Clean title for one file — cached on the doc, else basic+AI cleaned & persisted."""
    if doc.get("clean_name"):
        return doc["clean_name"]
    raw = doc.get("name") or ""
    clean = basic_clean(raw, doc.get("ext"))
    try:
        m = await ai.clean_titles([raw])
        ai_clean = (m or {}).get(raw)
        if ai_clean and len(ai_clean) >= 2:
            clean = ai_clean
    except Exception:  # noqa: BLE001
        pass
    await _persist_clean(doc.get("file_unique_id"), clean)
    doc["clean_name"] = clean
    return clean


async def clean_names_for(docs: list[dict]) -> dict[str, str]:
    """{file_unique_id: clean_name} for a list (e.g. a results page). Uses cached
    names, batch-AI-cleans the uncached ones in ONE call, and persists them."""
    out: dict[str, str] = {}
    missing: list[dict] = []
    for d in docs:
        fuid = d.get("file_unique_id")
        if not fuid:
            continue
        if d.get("clean_name"):
            out[fuid] = d["clean_name"]
        else:
            out[fuid] = basic_clean(d.get("name") or "", d.get("ext"))
            missing.append(d)
    if missing:
        try:
            m = await ai.clean_titles([d.get("name") or "" for d in missing])
        except Exception:  # noqa: BLE001
            m = {}
        for d in missing:
            fuid = d.get("file_unique_id")
            raw = d.get("name") or ""
            ai_clean = (m or {}).get(raw)
            final = ai_clean if (ai_clean and len(ai_clean) >= 2) else out[fuid]
            out[fuid] = final
            d["clean_name"] = final
            await _persist_clean(fuid, final)
    return out


def has_uncached(docs: list[dict]) -> bool:
    return any(not d.get("clean_name") for d in docs)


# ── caption ─────────────────────────────────────────────────────────────────────────
async def branded_caption(clean: str, ext: str | None = None, note: str = "") -> str:
    h = await handle()
    head = f"{icon_for(ext or '')} <b>{escape(clean)}</b>"
    if h:
        head += f"  {escape(h)}"
    return head + (f"\n{note}" if note else "")


# ── thumbnail ─────────────────────────────────────────────────────────────────────────
def _process_thumb(raw: bytes) -> bytes | None:
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(raw))
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((_THUMB_MAX_PX, _THUMB_MAX_PX))
        for q in (85, 70, 55, 40, 30):
            buf = BytesIO()
            im.save(buf, "JPEG", quality=q)
            if buf.tell() <= _THUMB_MAX_BYTES:
                return buf.getvalue()
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — Pillow missing / bad image
        return None


async def _thumb_bytes(bot) -> bytes | None:
    """Processed JPEG bytes of the admin branding image (cached), or None."""
    fid = await thumb_file_id()
    if not fid:
        return None
    if fid in _thumb_cache:
        return _thumb_cache[fid]
    try:
        bio = await bot.download(fid)
        raw = bio.read() if bio else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("brand thumb download failed: %s", exc)
        return None
    if not raw:
        return None
    out = await asyncio.get_running_loop().run_in_executor(None, _process_thumb, raw)
    if out:
        _thumb_cache.clear()        # keep only the current image cached
        _thumb_cache[fid] = out
    return out


# ── re-upload (bake thumbnail + clean filename) ──────────────────────────────────────
def _safe_filename(clean: str, ext: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", clean).strip()[:120] or "book"
    return f"{base}.{ext}" if ext else base


async def _get_bytes(bot, doc: dict, chan: int) -> bytes | None:
    """Bot-usable bytes for re-upload (≤20MB), via the stored file_id or, failing
    that, a one-time staging copy in the file channel. None if not feasible."""
    fid = doc.get("file_id")
    if fid:
        try:
            meta = await bot.get_file(fid)
            if not (meta.file_size and meta.file_size > _DOWNLOAD_CAP):
                bio = await bot.download(fid)
                return bio.read() if bio else None
        except Exception:  # noqa: BLE001 — id not bot-usable / too big → try staging
            pass
    # staging copy: copy the original channel message to get a bot-usable id, then
    # download and delete the temp copy. Works for Telethon-coords legacy files.
    src, msg = doc.get("chan_id") or chan, doc.get("msg_id")
    if not (src and msg):
        return None
    staging = None
    try:
        staging = await bot.copy_message(chan, src, msg, caption=PREP_MARKER,
                                         disable_notification=True)
        sdoc = staging.document
        if not sdoc:
            return None
        meta = await bot.get_file(sdoc.file_id)
        if meta.file_size and meta.file_size > _DOWNLOAD_CAP:
            return None
        bio = await bot.download(sdoc.file_id)
        return bio.read() if bio else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("staging fetch failed for %s: %s", doc.get("file_unique_id"), exc)
        return None
    finally:
        if staging is not None:
            try:
                await bot.delete_message(chan, staging.message_id)
            except Exception:  # noqa: BLE001
                pass


async def _build_prepared(bot, doc: dict, clean: str) -> dict | None:
    """Re-upload the file with the admin thumbnail + clean filename + branded caption.
    Returns prepared coords, or None to fall back to caption-only branding."""
    if not await thumb_enabled():
        return None
    ext = (doc.get("ext") or "").lower()
    if (doc.get("kind") or "document") != "document" or ext not in _BOOK_EXT:
        return None   # only re-brand book documents
    chan = await get_file_channel()
    if not chan:
        return None
    thumb = await _thumb_bytes(bot)
    if not thumb:
        return None
    data = await _get_bytes(bot, doc, chan)
    if not data or len(data) < 256:
        return None
    cap = PREP_MARKER + await branded_caption(clean, ext)
    try:
        sent = await bot.send_document(
            chan, BufferedInputFile(data, filename=_safe_filename(clean, ext)),
            thumbnail=BufferedInputFile(thumb, filename="cover.jpg"),
            caption=cap, disable_notification=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prepared re-upload failed for %s: %s", doc.get("file_unique_id"), exc)
        return None
    pid = sent.document.file_id if sent.document else None
    return {"prepared_msg_id": sent.message_id, "prepared_file_id": pid, "chan": chan}


async def ensure_prepared(bot, doc: dict) -> dict:
    """Resolve the best deliverable coordinates for a file, branding it (once) along
    the way. Returns {chan_id, msg_id, file_id, clean}. Never raises."""
    fuid = doc.get("file_unique_id")
    db = await MongoManager.get()
    base = (await db.find_one_global("files", {"file_unique_id": fuid}) if fuid else None) or doc
    clean = await ensure_clean(base)
    chan = await get_file_channel()

    # already prepared?
    if base.get("prepared_msg_id"):
        return {"chan_id": chan or base.get("chan_id"), "msg_id": base["prepared_msg_id"],
                "file_id": base.get("prepared_file_id"), "clean": clean}

    # attempt one-time heavy prep (unless we've already decided caption-only)
    if await brand_enabled() and base.get("brand_state") != "caption":
        try:
            prep = await _build_prepared(bot, base, clean)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_prepared error for %s: %s", fuid, exc)
            prep = None
        if prep:
            if fuid:
                await db.safe_update(
                    "files", {"file_unique_id": fuid},
                    {"$set": {"prepared_msg_id": prep["prepared_msg_id"],
                              "prepared_file_id": prep["prepared_file_id"],
                              "brand_state": "prepared"}}, upsert=False)
            return {"chan_id": prep["chan"], "msg_id": prep["prepared_msg_id"],
                    "file_id": prep["prepared_file_id"], "clean": clean}
        # mark caption-only so we don't keep retrying the expensive path
        if fuid:
            await db.safe_update("files", {"file_unique_id": fuid},
                                 {"$set": {"brand_state": "caption"}}, upsert=False)

    return {"chan_id": base.get("chan_id") or doc.get("chan_id") or chan,
            "msg_id": base.get("msg_id") or doc.get("msg_id"),
            "file_id": base.get("file_id") or doc.get("file_id"), "clean": clean}


async def _needs_prep(fuid: str) -> bool:
    """Decide (from the CANONICAL files doc, so a Favorite/Finished copy of an
    already-branded file doesn't flicker 'Preparing…') whether work remains."""
    if not fuid:
        return True
    db = await MongoManager.get()
    st = await db.find_one_global(
        "files", {"file_unique_id": fuid},
        {"clean_name": 1, "prepared_msg_id": 1, "brand_state": 1})
    if not st:
        return True
    return (not st.get("clean_name")) or (
        not st.get("prepared_msg_id") and st.get("brand_state") != "caption")


# ── the single delivery entry point ──────────────────────────────────────────────────
async def deliver(bot, uid: int, doc: dict, *, reply_markup=None, note: str = "") -> bool:
    """Brand and deliver a file to a user. Shows a one-time 'Preparing…' message when
    work is needed, then sends the branded file (cover thumbnail where possible,
    clean caption everywhere). Returns whether it was delivered. Never raises."""
    prep_msg = None
    try:
        if await brand_enabled() and await _needs_prep(doc.get("file_unique_id")):
            try:
                prep_msg = await bot.send_message(
                    uid, "📤 <b>Preparing your file…</b>\n"
                    "<i>Tidying the title and adding the cover — one moment.</i>")
            except Exception:  # noqa: BLE001
                prep_msg = None

        coords = await ensure_prepared(bot, doc)
        caption = await branded_caption(coords["clean"], doc.get("ext"), note=note)

        delivered = False
        try:
            if coords.get("chan_id") and coords.get("msg_id"):
                await bot.copy_message(uid, coords["chan_id"], coords["msg_id"],
                                       caption=caption, reply_markup=reply_markup)
                delivered = True
            elif coords.get("file_id"):
                await bot.send_document(uid, coords["file_id"], caption=caption,
                                        reply_markup=reply_markup)
                delivered = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("branded delivery failed for %s: %s", doc.get("file_unique_id"), exc)
        return delivered
    finally:
        if prep_msg is not None:
            try:
                await bot.delete_message(uid, prep_msg.message_id)
            except Exception:  # noqa: BLE001
                pass
