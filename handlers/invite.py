"""
handlers/invite.py — public logs access via a one-time invite link.

📜 Public Logs (or /get_link) → a 24h, single-use invite to the logs channel,
limited to one link per user per day. The bot must be an admin (with invite
permission) in LOG_CHANNEL_ID.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import LOG_CHANNEL_ID
from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


async def _give_link(bot, uid: int):
    if not LOG_CHANNEL_ID:
        return ("📜 <b>Public Logs</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>This area isn't open just yet.</i>\n"
                "<blockquote>⚠️ Public logs haven't been configured by the team.\n"
                "Please check back soon — we'll have it ready for you.</blockquote>", None)
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid}, {"last_invite_at": 1}) or {}
    last = u.get("last_invite_at")
    now = datetime.now(timezone.utc)
    if last and (now - last).total_seconds() < 86400:
        left = 86400 - int((now - last).total_seconds())
        return (f"📜 <b>Public Logs</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>You're already set up for today.</i>\n"
                f"<blockquote>⏳ One private link per day keeps access secure.\n"
                f"Your next link unlocks in <code>{left // 3600}h {(left % 3600) // 60}m</code>.</blockquote>", None)
    try:
        link = await bot.create_chat_invite_link(
            LOG_CHANNEL_ID, expire_date=now + timedelta(hours=24), member_limit=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("invite link failed: %s", exc)
        return ("📜 <b>Public Logs</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>⚠️ We couldn't create your link this moment.\n"
                "Give it a few seconds and try again — it should sort itself out.</blockquote>", None)
    await db.safe_update("users", {"user_id": uid}, {"$set": {"last_invite_at": now}})
    return (f"📜 <b>Public Logs — Private Access</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Your personal pass to the logs channel is ready.</i>\n\n"
            f"🔗 <b>Your access link</b>\n{link.invite_link}\n\n"
            f"<blockquote>🔒 <b>Single-use</b> — works for one person only.\n"
            f"⏳ <b>Valid 24 hours</b>, then it expires automatically.</blockquote>\n"
            f"<i>💡 Open it on this device for a smooth join.</i>", None)


@router.message(Command("get_link"))
async def cmd_get_link(message: Message) -> None:
    text, _ = await _give_link(message.bot, message.chat.id)
    await message.answer(text)


@router.callback_query(F.data == "tool_logs")
async def cb_logs(call: CallbackQuery) -> None:
    await call.answer()
    text, _ = await _give_link(call.bot, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb([btn("🔙 Back", "menu_tools",
                                                           style="danger")]))
