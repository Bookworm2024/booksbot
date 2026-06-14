"""
middlewares/maintenance.py — block non-admins when maintenance mode is ON.

Reads the `maintenance` flag from Mongo kv (toggled by admins). Admins always
pass through so they can keep working / turn it back off.
"""
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import ADMIN_IDS
from database.connection import MongoManager

logger = logging.getLogger(__name__)

_MSG = ("🛠 <b>We're doing quick maintenance.</b>\nPlease check back in a few "
        "minutes — your balance and library are safe.")


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        uid = getattr(user, "id", None)
        if uid and uid not in ADMIN_IDS:
            try:
                db = await MongoManager.get()
                if await db.kv_get("maintenance", False):
                    if isinstance(event, Message):
                        await event.answer(_MSG)
                    elif isinstance(event, CallbackQuery):
                        await event.answer("🛠 Under maintenance — back shortly.", show_alert=True)
                    return None
            except Exception:  # noqa: BLE001 — never block traffic on a check error
                pass
        return await handler(event, data)
