"""
handlers/support.py — user ↔ admin support.

  /support (or 🆘 Support) → user sends ONE message (text or photo) →
  forwarded to all admins with a 💬 Reply button. Admin taps Reply, types a
  message, and it's delivered back to the user. No third-party services.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from utils.keyboards import btn, kb

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
        "🆘 <b>Support</b>\n\nDescribe your issue in <b>one message</b> "
        "(you can attach a screenshot). Send /cancel to abort.",
        reply_markup=kb([btn("❌ Cancel", "menu_account", style="danger")]))


@router.message(SupportFSM.awaiting_message, F.text | F.photo)
async def on_support_msg(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Support request cancelled.")
        return
    await state.clear()
    uid = message.chat.id
    name = message.from_user.first_name or "User"
    body = message.text or message.caption or "<i>(no text)</i>"
    header = (f"📩 <b>Support Request</b>\n"
              f"👤 <a href='tg://user?id={uid}'>{name}</a> (<code>{uid}</code>)\n\n"
              f"💬 {body}")
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
    await message.answer("✅ <b>Sent to our team.</b> We'll get back to you shortly.")


@router.callback_query(F.data.startswith("sup_reply:"))
async def cb_reply(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    target = int(call.data.split(":", 1)[1])
    await call.answer()
    await state.set_state(SupportFSM.awaiting_reply)
    await state.update_data(target=target)
    await call.message.answer(f"💬 Type your reply to <code>{target}</code>:")


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
            "📩 <b>Reply from Support</b>\n\n"
            f"💬 {message.text}\n\n<i>Use /support to respond.</i>")
        await message.answer("✅ Reply delivered.")
    except Exception:  # noqa: BLE001
        await message.answer("❌ Couldn't deliver — the user may have blocked the bot.")
