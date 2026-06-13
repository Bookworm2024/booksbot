"""
middlewares/ban.py — drop updates from banned users early.

Registered on both message and callback_query observers. Admins are never
blocked (so a mistakenly self-banned admin can still recover).
"""
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from config import ADMIN_IDS
from utils.users import is_banned

logger = logging.getLogger(__name__)


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        uid = getattr(user, "id", None)
        if uid and uid not in ADMIN_IDS and await is_banned(uid):
            # Silently swallow banned users' updates (don't spam them).
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("🚫 You are banned.", show_alert=True)
                except Exception:  # noqa: BLE001
                    pass
            return None
        return await handler(event, data)
