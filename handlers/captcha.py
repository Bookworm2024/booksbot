"""
handlers/captcha.py — safe in-house anti-bot gate.

Replaces the original bot's third-party "live verification" (which leaked the
bot token + webhook to an external site). This one is self-contained: an
emoji-tap challenge whose expected answer is stored server-side, so the answer
never travels to the client. Gated by CAPTCHA_ENABLED; verification lasts
CAPTCHA_TTL_SECONDS.
"""
import logging
import random
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import CAPTCHA_ENABLED, CAPTCHA_TTL_SECONDS
from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_EMOJIS = ["🍎", "🚀", "🐘", "🎲", "🌙", "🔥", "🎧", "📚", "⚽", "🦊"]


async def needs_verification(uid: int) -> bool:
    if not CAPTCHA_ENABLED:
        return False
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"captcha_at": 1})
    last = doc.get("captcha_at") if doc else None
    if not last:
        return True
    return (datetime.now(timezone.utc) - last).total_seconds() > CAPTCHA_TTL_SECONDS


async def send_challenge(message, uid: int) -> None:
    """Pick 4 emojis, store the target server-side, ask the user to tap it."""
    choices = random.sample(_EMOJIS, 4)
    target = random.choice(choices)
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid}, {"$set": {"captcha_target": target}})
    await message.answer(
        "🛡 <b>One quick check</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A two-second tap keeps your library safe — then we'll take it from here.</i>\n\n"
        f"<blockquote>Tap the <b>{target}</b> from the row below to confirm you're "
        "human.\n\n"
        "<i>🛡 We verify this privately on our side — nothing about you ever leaves "
        "the bot.</i></blockquote>",
        reply_markup=kb([btn(e, f"cap:{e}", style="primary") for e in choices]))


@router.callback_query(F.data.startswith("cap:"))
async def cb_solve(call: CallbackQuery) -> None:
    picked = call.data.split(":", 1)[1]
    uid = call.from_user.id
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"captcha_target": 1})
    target = (doc or {}).get("captcha_target")
    if picked != target:
        await call.answer("❌ Not quite — here's a fresh set. Tap the matching emoji to continue.", show_alert=True)
        await call.message.delete()
        await send_challenge(call.message, uid)
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"captcha_at": datetime.now(timezone.utc),
                                   "captcha_target": None}})
    await call.answer("✅ Verified — welcome in. Opening your library now.")
    await call.message.delete()
    # render the dashboard now (lazy import avoids a circular dependency)
    from handlers.start import _send_dashboard
    await _send_dashboard(call.message, call.from_user.first_name or "Reader")
