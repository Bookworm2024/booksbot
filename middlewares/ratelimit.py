"""
middlewares/ratelimit.py — per-user flood protection (anti-abuse).

A lightweight in-memory sliding-window limiter that runs on every message and
callback. If a user fires more than `flood_max` actions within
`flood_window_sec`, further updates are dropped (with a single, non-spammy
"slow down" notice per window) until they ease off.

Why in-memory (not Mongo): this runs on EVERY update, so a DB round-trip per
update would be far too costly. Losing the window on restart is harmless — it's
flood protection, not state. Thresholds ARE admin-editable (utils.settings:
flood_max / flood_window_sec) and are cached here with a short TTL so the hot
path stays O(1). Set flood_max very high to effectively disable.

Admins (config.ADMIN_IDS) are never limited.
"""
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import ADMIN_IDS
from utils.settings import get_float

logger = logging.getLogger(__name__)

# uid → list of recent action timestamps (monotonic seconds), pruned per access
_hits: Dict[int, list] = {}
# uid → last time we warned them (so the warning itself can't be spammed)
_warned: Dict[int, float] = {}

# cached thresholds (refreshed from settings at most every _TTL seconds)
_cache = {"t": 0.0, "max": 20, "win": 10.0}
_TTL = 30.0
# safety valve: keep the hit map from growing unbounded across many users
_MAX_TRACKED = 50_000


async def _limits() -> tuple[int, float]:
    now = time.monotonic()
    if now - _cache["t"] >= _TTL:
        _cache["t"] = now
        try:
            _cache["max"] = max(1, int(await get_float("flood_max")))
            _cache["win"] = max(1.0, float(await get_float("flood_window_sec")))
        except Exception:  # noqa: BLE001 — never break traffic on a settings read
            pass
    return _cache["max"], _cache["win"]


async def _warn(event: TelegramObject, uid: int, win: float) -> None:
    now = time.monotonic()
    if now - _warned.get(uid, 0.0) < win:
        return  # already warned this window
    _warned[uid] = now
    try:
        if isinstance(event, CallbackQuery):
            await event.answer("⏳ Slow down a moment…", show_alert=False)
        elif isinstance(event, Message):
            await event.answer("⏳ <b>Slow down a moment.</b> You're acting too fast — "
                               "try again in a few seconds.")
    except Exception:  # noqa: BLE001
        pass


class RateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        uid = getattr(user, "id", None)
        if uid is None or uid in ADMIN_IDS:
            return await handler(event, data)

        mx, win = await _limits()
        now = time.monotonic()
        if len(_hits) > _MAX_TRACKED:   # crude safety reset (very rare)
            _hits.clear()
            _warned.clear()  # else _warned grows unbounded (never pruned otherwise)
        hits = _hits.setdefault(uid, [])
        cutoff = now - win
        # prune timestamps outside the window
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= mx:
            await _warn(event, uid, win)
            return None  # drop — don't run the handler
        hits.append(now)
        return await handler(event, data)
