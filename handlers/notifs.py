"""
handlers/notifs.py — notification preferences.

Account → 🔔 Notifications → toggle re-engagement reminders on/off. Default ON
(only an explicit False disables, so existing users are opted in).
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


async def _view(uid: int):
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"notif": 1}) or {}
    on = doc.get("notif") is not False
    text = ("<b>🔔 Notifications</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Reminders: <b>{'🟢 ON' if on else '🔴 OFF'}</b>\n\n"
            "<i>We only nudge you when your free daily rewards are waiting — never spam.</i>")
    toggle = btn("🔴 Turn OFF", "notif_off", style="danger") if on \
        else btn("🟢 Turn ON", "notif_on", style="success")
    return text, kb([toggle], [btn("🔙 Back", "menu_account", style="primary")])


@router.message(Command("notifications"))
async def cmd_notifs(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "acc_notifs")
async def cb_notifs(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.in_({"notif_on", "notif_off"}))
async def cb_toggle(call: CallbackQuery) -> None:
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": call.from_user.id},
                         {"$set": {"notif": call.data == "notif_on"}})
    await call.answer("Updated")
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)
