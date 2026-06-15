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
from utils.admins import add_admin, get_extra_admins, remove_admin
from utils.audit import log_action
from utils.keyboards import btn, kb, webapp_btn
from utils.users import set_ban

logger = logging.getLogger(__name__)
router = Router()


class AdminFSM(StatesGroup):
    awaiting_ban_id = State()
    awaiting_unban_id = State()
    awaiting_add_admin = State()
    awaiting_remove_admin = State()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def _panel_kb(is_super: bool):
    rows = [
        [webapp_btn("📊 Dashboard", "admin.html", style="success", fallback_cb="admin_open")],
        [btn("🚫 Ban User", "admin_ban", style="danger"),
         btn("✅ Unban User", "admin_unban", style="success")],
        [btn("📬 Requests", "admin_requests", style="primary"),
         btn("📡 Broadcast", "admin_broadcast", style="primary")],
    ]
    if is_super:
        rows.append([btn("💰 Revenue", "admin_revenue", style="success"),
                     btn("⚙️ Pricing", "admin_pricing", style="success")])
        rows.append([btn("🔥 Flash Sale", "admin_deal", style="success"),
                     btn("⭐ Featured", "admin_featured", style="success")])
        rows.append([btn("➕ Add BGM", "admin_addbgm", style="success"),
                     btn("👤 User Lookup", "admin_userinfo", style="primary")])
        rows.append([btn("🎟️ Create Code", "admin_create", style="success"),
                     btn("🎮 Questions", "admin_qbank", style="primary")])
        rows.append([btn("🛠 Maintenance", "admin_maint", style="danger"),
                     btn("🎁 Bulk Grant", "admin_bulk", style="success")])
        rows.append([btn("🏷 Tag Genres", "admin_tag", style="primary"),
                     btn("🛡 Manage Admins", "admin_manage", style="primary")])
        rows.append([btn("🤖 AI Settings", "admin_ai", style="primary"),
                     btn("🧰 More Tools", "admin_more", style="primary")])
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
    await log_action(message.chat.id, "ban", target)
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
    await log_action(message.chat.id, "unban", target)
    await message.answer(f"✅ User <code>{target}</code> unbanned.")
    try:
        await message.bot.send_message(int(target), "✅ <b>Access Restored</b>")
    except Exception:  # noqa: BLE001
        pass


# ── manage admins (super admin) ─────────────────────────────────────────────────
# (admin_create is handled in economy.py; requests/broadcast/etc. live in their
#  own routers.)
async def _manage_text() -> str:
    extra = set(await get_extra_admins())
    lines = ["<b>🛡 Manage Admins</b>\n━━━━━━━━━━━━━━━━━━"]
    for a in sorted(ADMIN_IDS):
        tag = "👑 super" if a == SUPER_ADMIN_ID else ("➕ added" if a in extra else "🔧 env")
        lines.append(f"• <code>{a}</code> — {tag}")
    lines.append("\n<i>Added admins can be removed here. Env &amp; super admins are "
                 "fixed in config.</i>")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_manage")
async def cb_manage(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _manage_text(),
        reply_markup=kb([btn("➕ Add Admin", "adm_add", style="success"),
                         btn("➖ Remove Admin", "adm_remove", style="danger")],
                        [btn("🔙 Back", "admin_open", style="primary")]))


@router.callback_query(F.data == "adm_add")
async def cb_adm_add(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_add_admin)
    await call.message.answer("🆔 Send the <b>User ID</b> to promote to admin. /cancel to abort.")


@router.callback_query(F.data == "adm_remove")
async def cb_adm_remove(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_remove_admin)
    await call.message.answer("🆔 Send the <b>User ID</b> to remove from admins. /cancel to abort.")


@router.message(AdminFSM.awaiting_add_admin)
async def do_add_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if target.lower() == "/cancel":
        await message.answer("❌ Cancelled."); return
    if not target.isdigit():
        await message.answer("❌ Invalid numeric User ID."); return
    added = await add_admin(int(target))
    if added:
        await log_action(message.chat.id, "add_admin", target)
    await message.answer(
        f"✅ <code>{target}</code> is now an admin." if added
        else f"ℹ️ <code>{target}</code> was already an admin.",
        reply_markup=kb([btn("🛡 Manage Admins", "admin_manage", style="primary")]))
    if added:
        try:
            await message.bot.send_message(
                int(target), "🛡 <b>You're now an admin</b> of this bot. Open /admin.")
        except Exception:  # noqa: BLE001
            pass


@router.message(AdminFSM.awaiting_remove_admin)
async def do_remove_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if target.lower() == "/cancel":
        await message.answer("❌ Cancelled."); return
    if not target.isdigit():
        await message.answer("❌ Invalid numeric User ID."); return
    ok = await remove_admin(int(target))
    if ok:
        await log_action(message.chat.id, "remove_admin", target)
    await message.answer(
        f"✅ <code>{target}</code> removed from admins." if ok else
        f"⚠️ <code>{target}</code> isn't a removable admin "
        "(env &amp; super admins are fixed in config).",
        reply_markup=kb([btn("🛡 Manage Admins", "admin_manage", style="primary")]))
