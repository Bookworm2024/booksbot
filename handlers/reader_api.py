"""
handlers/reader_api.py — backend for the reader & audiobook Mini Apps.

Endpoints (all initData-authenticated):
  GET  /api/file?fuid=…&init_data=…   stream the file bytes (gated: the file
                                       must be in the caller's favorites)
  GET  /api/reader/state?fuid=…        → {page, position, bookmarks}
  POST /api/reader/state               persist page / audio position / bookmarks

Bot API note: getFile caps downloads at 20 MB on the official server. If the
file is bigger (or has no bot-usable file_id), we return 413 and the Mini App
tells the user to grab it in chat. Running a local Bot API server
(TELEGRAM_API_BASE) lifts that limit to ~2 GB.
"""
import logging
from datetime import datetime, timezone
from io import BytesIO

from aiohttp import web

from database.connection import MongoManager
from utils.webapp_auth import user_id_from

logger = logging.getLogger(__name__)

_CTYPE = {
    "pdf": "application/pdf", "epub": "application/epub+zip",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "m4b": "audio/mp4",
    "wav": "audio/wav", "ogg": "audio/ogg", "flac": "audio/flac",
}


async def _owned(uid: int, fuid: str) -> dict | None:
    """Return the file doc the caller is allowed to stream — one they've saved
    (favorites) OR downloaded (library) — else None. Both store the delivery
    fields (file_id/ext) the reader needs."""
    db = await MongoManager.get()
    doc = await db.find_one_global("favorites", {"user_id": uid, "file_unique_id": fuid})
    if doc:
        return doc
    return await db.find_one_global("library", {"user_id": uid, "file_unique_id": fuid})


async def api_file(request: web.Request) -> web.Response:
    uid = user_id_from(request.query.get("init_data", ""))
    fuid = request.query.get("fuid", "")
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    fav = await _owned(uid, fuid)
    if not fav:
        return web.json_response({"error": "not_in_library"}, status=403)

    file_id = fav.get("file_id")
    if not file_id:
        # backfilled-only file (no bot-usable id) — can't stream
        return web.json_response({"error": "stream_unavailable"}, status=409)

    bot = request.app["bot"]
    try:
        tg_file = await bot.get_file(file_id)
        buf = BytesIO()
        await bot.download_file(tg_file.file_path, buf)
        data = buf.getvalue()
    except Exception as exc:  # noqa: BLE001 — usually >20MB on official API
        logger.info("file stream failed (%s): %s", fuid, exc)
        return web.json_response({"error": "too_large"}, status=413)

    ext = (fav.get("ext") or "").lower()
    ctype = _CTYPE.get(ext, "application/octet-stream")
    return web.Response(body=data, content_type=ctype,
                        headers={"Cache-Control": "private, max-age=600",
                                 "Accept-Ranges": "bytes"})


async def api_reader_state_get(request: web.Request) -> web.Response:
    uid = user_id_from(request.query.get("init_data", ""))
    fuid = request.query.get("fuid", "")
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    db = await MongoManager.get()
    st = await db.find_one_global("reader_state", {"user_id": uid, "fuid": fuid}) or {}
    return web.json_response({"page": st.get("page", 0),
                             "position": st.get("position", 0),
                             "loc": st.get("loc", ""),
                             "bookmarks": st.get("bookmarks", [])})


async def api_reader_state_set(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    uid = user_id_from(body.get("init_data", ""))
    fuid = body.get("fuid", "")
    if not uid or not fuid:
        return web.json_response({"error": "auth_failed"}, status=401)
    update = {}
    if "page" in body:
        update["page"] = int(body["page"])
    if "position" in body:
        update["position"] = float(body["position"])
    if "loc" in body:                       # EPUB CFI location string
        update["loc"] = str(body["loc"])[:500]
    if "bookmarks" in body and isinstance(body["bookmarks"], list):
        # generic: int pages (PDF) or CFI strings (EPUB)
        update["bookmarks"] = body["bookmarks"][:200]
    if update:
        now = datetime.now(timezone.utc)
        update["updated_at"] = now
        db = await MongoManager.get()
        await db.safe_update("reader_state", {"user_id": uid, "fuid": fuid},
                             {"$set": {"user_id": uid, "fuid": fuid, **update}})
        # record today's reading activity for streaks (idempotent per day)
        await db.safe_update("users", {"user_id": uid},
                             {"$addToSet": {"reading_days": now.strftime("%Y-%m-%d")}})
    return web.json_response({"ok": True})
