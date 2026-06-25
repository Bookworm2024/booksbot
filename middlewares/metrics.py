"""
middlewares/metrics.py — count updates for the 🩺 Health view.

Outermost middleware: bumps cheap in-process counters then passes the update
through untouched. Bulletproof — a counting error can never drop an update.
"""
import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            from utils.metrics import incr
            incr("updates")
            if isinstance(event, Message):
                incr("messages")
            elif isinstance(event, CallbackQuery):
                incr("callbacks")
        except Exception:  # noqa: BLE001 — never let metrics break an update
            pass
        return await handler(event, data)
