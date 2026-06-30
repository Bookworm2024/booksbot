"""
handlers/games_api.py — aiohttp JSON API for the Mini-App games.

Both endpoints authenticate via Telegram initData (utils.webapp_auth) — the
client cannot assert its own user id. All scoring/limits live in utils.games.
"""
import logging

from aiohttp import web

from utils.games import VALID_GAMES, cancel_session, new_session, submit
from utils.webapp_auth import user_id_from

logger = logging.getLogger(__name__)


async def _json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


async def api_game_new(request: web.Request) -> web.Response:
    body = await _json(request)
    uid = user_id_from(body.get("init_data", ""))
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    game = body.get("game")
    if game not in VALID_GAMES:
        return web.json_response({"error": "bad_game"}, status=400)
    level = body.get("level", "beginner")
    if level not in ("beginner", "moderate", "advanced"):
        level = "beginner"
    result = await new_session(uid, game, level)
    return web.json_response(result)


async def api_game_submit(request: web.Request) -> web.Response:
    body = await _json(request)
    uid = user_id_from(body.get("init_data", ""))
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    sid = body.get("session_id", "")
    answers = body.get("answers", [])
    if not isinstance(answers, list):
        answers = []
    result = await submit(uid, sid, answers)
    return web.json_response(result)


async def api_game_cancel(request: web.Request) -> web.Response:
    body = await _json(request)
    uid = user_id_from(body.get("init_data", ""))
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    sid = body.get("session_id", "")
    return web.json_response(await cancel_session(uid, sid))
