"""
handlers/bookle_api.py — JSON API for the Bookle Mini App (initData-auth).
"""
import logging

from aiohttp import web

from utils.bookle import get_or_create, guess
from utils.webapp_auth import user_id_from

logger = logging.getLogger(__name__)


async def _json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


async def api_bookle_new(request: web.Request) -> web.Response:
    body = await _json(request)
    uid = user_id_from(body.get("init_data", ""))
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    return web.json_response(await get_or_create(uid))


async def api_bookle_guess(request: web.Request) -> web.Response:
    body = await _json(request)
    uid = user_id_from(body.get("init_data", ""))
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    return web.json_response(await guess(uid, body.get("guess", "")))
