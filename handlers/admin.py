"""
handlers/admin.py — /admin centre (foundation).

Two roles:
  • Super admin (config.SUPER_ADMIN_ID) — full control.
  • Normal admins (config.ADMIN_IDS) — a subset (request handling etc.),
    granted by the super admin.

This phase ships the gated entry panel + ban/unban (the most-used moderation
action). Request management, broadcast, redeem-code creation, game/AI/voting
panels are tracked in PLAN.md for later phases.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, SUPER_ADMIN_ID
from utils.keyboards import btn, kb
from utils.users import set_ban

logger = logging.getLogger(__name__)
router = Router()


class AdminFSM(StatesGroup):
    awaiting_ban_id = State()
    awaiting_unban_id = State()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def _panel_kb(is_super: bool):
    rows = [
        [btn("🚫 Ban User", "admin_ban", style="danger"),
         btn("✅ Unban User", "admin_unban", style="success")],
        [btn("📬 Requests", "admin_requests", style="primary"),
         btn("📡 Broadcast", "admin_broadcast", style="primary")],
    ]
    if is_super:
        rows.append([btn("💰 Revenue", "admin_revenue", style="success"),
                     btn("⚙️ Pricing", "admin_pricing", style="success")])
        rows.append([btn("➕ Add BGM", "admin_addbgm", style="success"),
                     btn("🎟️ Create Code", "admin_create", style="success")])
        rows.append([btn("🎮 Questions", "admin_qbank", style="primary"),
                     btn("🛡 Manage Admins", "admin_manage", style="primary")])
    return kb(*rows)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    uid = message.chat.id
    if not _is_admin(uid):
        await message.answer("🚫 <b>Access Denied.</b>")
        return
    is_super = uid == SUPER_ADMIN_ID
    title = "👑 Super Admin Panel" if is_super else "🛡 Admin Panel"
    await message.answer(f"<b>{title}</b>\n\nSelect an action:", reply_markup=_panel_kb(is_super))


@router.callback_query(F.data == "admin_open")
async def cb_admin_open(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    is_super = call.from_user.id == SUPER_ADMIN_ID
    title = "👑 Super Admin Panel" if is_super else "🛡 Admin Panel"
    await call.message.edit_text(f"<b>{title}</b>\n\nSelect an action:",
                                 reply_markup=_panel_kb(is_super))


# ── ban / unban ───────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_ban")
async def cb_ban(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_ban_id)
    await call.message.answer("🆔 Send the <b>User ID</b> to ban:")


@router.callback_query(F.data == "admin_unban")
async def cb_unban(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_unban_id)
    await call.message.answer("🆔 Send the <b>User ID</b> to unban:")


@router.message(AdminFSM.awaiting_ban_id)
async def do_ban(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if not target.isdigit():
        await message.answer("❌ Invalid numeric User ID.")
        return
    await set_ban(int(target), True)
    await message.answer(f"✅ User <code>{target}</code> banned.")
    try:
        await message.bot.send_message(int(target), "🚫 <b>Access Revoked</b>\nYou have been banned.")
    except Exception:  # noqa: BLE001
        pass


@router.message(AdminFSM.awaiting_unban_id)
async def do_unban(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if not target.isdigit():
        await message.answer("❌ Invalid numeric User ID.")
        return
    await set_ban(int(target), False)
    await message.answer(f"✅ User <code>{target}</code> unbanned.")
    try:
        await message.bot.send_message(int(target), "✅ <b>Access Restored</b>")
    except Exception:  # noqa: BLE001
        pass


# ── stubs for later phases ──────────────────────────────────────────────────────
# admin_requests → requests_manual · admin_broadcast → broadcast · admin_qbank → qadmin
@router.callback_query(F.data.in_({"admin_addbgm", "admin_create", "admin_manage"}))
async def cb_admin_stub(call: CallbackQuery) -> None:
    await call.answer("Coming in a later phase.", show_alert=True)
