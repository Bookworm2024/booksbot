"""
handlers/support.py — user ↔ admin support.

  /support (or 🆘 Support) → user sends ONE message (text or photo) →
  forwarded to all admins with a 💬 Reply button. Admin taps Reply, types a
  message, and it's delivered back to the user. No third-party services.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from utils.keyboards import btn, kb
from utils.permissions import has

logger = logging.getLogger(__name__)
router = Router()


class SupportFSM(StatesGroup):
    awaiting_message = State()
    awaiting_reply = State()


def _reply_kb(uid: int):
    return kb([btn("💬 Reply", f"sup_reply:{uid}", style="success")])


@router.message(Command("support"))
async def cmd_support(message: Message, state: FSMContext) -> None:
    await _open(message, state)


@router.callback_query(F.data == "menu_support")
async def cb_support(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _open(call.message, state)


async def _open(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportFSM.awaiting_message)
    await message.answer(
        "🆘 <b>Support Inbox</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A real person reads every message — we'll take it from here.</i>\n\n"
        "<blockquote>📝 Describe what's happening in <b>one message</b>. "
        "Add a screenshot if it helps us see the same screen you do.\n"
        "💬 We reply right here in chat, usually within a few hours.\n"
        "🛑 Changed your mind? Tap <b>Cancel</b> below anytime.</blockquote>\n"
        "<i>💡 The more detail you share — a book title, a code, what you tapped — the faster we can fix it.</i>",
        reply_markup=kb([btn("❌ Cancel", "menu_account", style="danger")]))


@router.message(SupportFSM.awaiting_message, F.text | F.photo)
async def on_support_msg(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.answer(
            "🆘 <b>Support Cancelled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No message was sent — your inbox is clear.</i>\n\n"
            "<i>💡 Need us again? Just open <b>Support</b> whenever you're ready.</i>")
        return
    await state.clear()
    uid = message.chat.id
    name = escape(message.from_user.first_name or "User")
    raw_body = message.text or message.caption
    body = escape(raw_body) if raw_body else "<i>(no text)</i>"
    header = (f"📩 <b>New Support Request</b>\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"👤 From <a href='tg://user?id={uid}'>{name}</a> · <code>{uid}</code>\n\n"
              f"<blockquote>💬 {body}</blockquote>\n"
              f"<i>Tap 💬 Reply below to respond — it lands straight in their chat.</i>")
    photo = message.photo[-1].file_id if message.photo else None
    for admin in ADMIN_IDS:
        try:
            if photo:
                await message.bot.send_photo(admin, photo, caption=header,
                                             reply_markup=_reply_kb(uid))
            else:
                await message.bot.send_message(admin, header, reply_markup=_reply_kb(uid))
        except Exception:  # noqa: BLE001
            pass
    await message.answer(
        "✅ <b>Message Received</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your request is with our team — we'll take it from here.</i>\n\n"
        "<blockquote>📬 Watch this chat for our reply — it usually arrives within a few hours.\n"
        "🔔 You'll get a notification the moment we respond.</blockquote>\n"
        "<i>💡 Thanks for the detail — it helps us sort it out faster.</i>")


@router.callback_query(F.data.startswith("sup_reply:"))
async def cb_reply(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "requests"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    target = int(call.data.split(":", 1)[1])
    await call.answer()
    await state.set_state(SupportFSM.awaiting_reply)
    await state.update_data(target=target)
    await call.message.answer(
        "💬 <b>Compose Reply</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Replying to <code>{target}</code> — your next message goes straight to them.</i>\n\n"
        "<i>💡 Keep it warm and specific; it lands in their chat exactly as you type it.</i>")


@router.message(SupportFSM.awaiting_reply, F.text)
async def on_reply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = data.get("target")
    await state.clear()
    if not target:
        return
    try:
        await message.bot.send_message(
            target,
            "📩 <b>Reply from Support</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Our team got back to you — here's their note.</i>\n\n"
            f"<blockquote>💬 {escape(message.text)}</blockquote>\n"
            "<i>💡 Still need a hand? Open <b>Support</b> to keep the conversation going.</i>")
        await message.answer(
            "✅ <b>Reply Delivered</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Your message landed in their chat.</i>")
    except Exception:  # noqa: BLE001
        await message.answer(
            "❌ <b>Couldn't Deliver</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>The user may have blocked the bot or closed the chat — nothing was sent.</i>")
