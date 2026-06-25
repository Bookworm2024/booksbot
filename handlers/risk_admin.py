"""
handlers/risk_admin.py — admin fraud / risk panel.

Admin → 🧰 More Tools → 🚨 Risk: review auto-flagged accounts (velocity / multi-
account signals), flag/unflag manually. Flagged users are blocked from gifting.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from utils.audit import log_action
from utils.keyboards import btn, kb
from utils.risk import flag_user, flagged_users, is_flagged, unflag_user

logger = logging.getLogger(__name__)
router = Router()


class RiskFSM(StatesGroup):
    flag_id = State()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def _panel():
    flagged = await flagged_users(20)
    lines = ["🚨 <b>Risk &amp; Fraud</b>\n━━━━━━━━━━━━━━━━━━"]
    rows = []
    if not flagged:
        lines.append("<i>No flagged accounts. 🎉</i>")
    for u in flagged:
        uid = u.get("user_id")
        who = escape((u.get("first_name") or "User")[:18])
        reason = escape((u.get("risk_reason") or "manual")[:50])
        lines.append(f"🚩 <code>{uid}</code> · {who}\n   <i>{reason}</i>")
        rows.append([btn(f"✅ Unflag {uid}", f"risk_unflag:{uid}", style="success")])
    rows.append([btn("🚩 Flag a User", "risk_flag", style="danger")])
    rows.append([btn("🔄 Refresh", "admin_risk", style="primary"),
                 btn("🔙 More Tools", "admin_more", style="primary")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_risk")
async def cb_risk(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("risk_unflag:"))
async def cb_unflag(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    try:
        uid = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    await unflag_user(uid)
    await log_action(call.from_user.id, "risk_unflag", str(uid))
    await call.answer("Unflagged")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "risk_flag")
async def cb_flag(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(RiskFSM.flag_id)
    await call.message.answer("🚩 Send the <b>User ID</b> to flag (blocks gifting). /cancel to abort.")


@router.message(RiskFSM.flag_id, F.text)
async def on_flag(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    if not raw.isdigit():
        await message.answer("⚠️ Send a numeric User ID."); return
    uid = int(raw)
    await flag_user(uid, f"manual flag by {message.chat.id}")
    await log_action(message.chat.id, "risk_flag", str(uid))
    text, markup = await _panel()
    await message.answer(f"🚩 Flagged <code>{uid}</code>.\n\n" + text, reply_markup=markup)
