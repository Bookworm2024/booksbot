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
        [webapp_btn("📊 Revenue Dashboard", "admin.html", style="success", fallback_cb="admin_open")],
        [btn("🚫 Ban User", "admin_ban", style="danger"),
         btn("✅ Unban User", "admin_unban", style="success")],
        [btn("📬 Request Queue", "admin_requests", style="primary"),
         btn("📡 Broadcast", "admin_broadcast", style="primary")],
    ]
    if is_super:
        rows.append([btn("💰 Revenue", "admin_revenue", style="success"),
                     btn("⚙️ Live Pricing", "admin_pricing", style="success")])
        rows.append([btn("🔥 Flash Sale", "admin_deal", style="success"),
                     btn("⭐ Featured Slots", "admin_featured", style="success")])
        rows.append([btn("➕ Add BGM", "admin_addbgm", style="success"),
                     btn("👤 User Lookup", "admin_userinfo", style="primary")])
        rows.append([btn("🎟️ Create Code", "admin_create", style="success"),
                     btn("🎮 Question Bank", "admin_qbank", style="primary")])
        rows.append([btn("🛠 Maintenance", "admin_maint", style="danger"),
                     btn("🎁 Bulk Grant", "admin_bulk", style="success")])
        rows.append([btn("🏷 Tag Genres", "admin_tag", style="primary"),
                     btn("🛡 Manage Admins", "admin_manage", style="primary")])
        rows.append([btn("🗂 File Channel", "admin_filechan", style="primary"),
                     btn("📥 Import Old Files", "admin_import", style="success")])
        rows.append([btn("🤖 AI Settings", "admin_ai", style="primary"),
                     btn("🧰 More Tools", "admin_more", style="primary")])
    return kb(*rows)


def _panel_text(is_super: bool) -> str:
    if is_super:
        return (
            "👑 <b>Owner Control Centre</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Full command of Books Provider — revenue, pricing, people and content, all in one console.</i>\n"
            "<blockquote>📊 <b>Dashboard</b> — live revenue, growth and health at a glance.\n"
            "🛡 <b>Moderation</b> — ban, unban and keep the community safe.\n"
            "📬 <b>Operations</b> — work the request queue and reach members by broadcast.\n"
            "💰 <b>Economy</b> — tune live pricing, run flash sales and grant 💎 BGM.\n"
            "🗂 <b>Content</b> — manage the file channel, import archives and tag genres.</blockquote>\n"
            "<i>💡 Pick a tool below — every action is logged to your audit trail.</i>"
        )
    return (
        "🛡 <b>Admin Console</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your day-to-day toolkit for keeping Books Provider running smoothly.</i>\n"
        "<blockquote>📊 <b>Dashboard</b> — see how the bot is performing.\n"
        "🛡 <b>Moderation</b> — ban or unban members when needed.\n"
        "📬 <b>Requests</b> — clear the queue and broadcast to members.</blockquote>\n"
        "<i>💡 Choose a tool below to get started.</i>"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    uid = message.chat.id
    if not _is_admin(uid):
        await message.answer(
            "🔒 <b>Restricted Area</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>This is the staff control centre — it's reserved for the Books Provider team.</i>\n"
            "<blockquote>If you're looking for books, games or your wallet, tap /start and the full member menu is yours.</blockquote>")
        return
    is_super = uid == SUPER_ADMIN_ID
    await message.answer(_panel_text(is_super), reply_markup=_panel_kb(is_super))


@router.callback_query(F.data == "admin_open")
async def cb_admin_open(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is reserved for the Books Provider team.", show_alert=True)
        return
    await call.answer()
    is_super = call.from_user.id == SUPER_ADMIN_ID
    await call.message.edit_text(_panel_text(is_super),
                                 reply_markup=_panel_kb(is_super))


# ── ban / unban ───────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_ban")
async def cb_ban(call: CallbackQuery, state: FSMContext) -> None:
    from utils.permissions import has
    if not await has(call.from_user.id, "ban"):
        await call.answer("You don't have the moderation permission for this.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_ban_id)
    await call.message.answer(
        "🚫 <b>Ban a Member</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Send the numeric User ID of the member you'd like to ban.</i>\n"
        "<blockquote>They'll immediately lose access to the bot and be notified that their access was revoked. You can lift the ban any time from Unban.</blockquote>\n"
        "<i>💡 Tip: grab the ID from 👤 User Lookup if you're not sure.</i>")


@router.callback_query(F.data == "admin_unban")
async def cb_unban(call: CallbackQuery, state: FSMContext) -> None:
    from utils.permissions import has
    if not await has(call.from_user.id, "ban"):
        await call.answer("You don't have the moderation permission for this.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_unban_id)
    await call.message.answer(
        "✅ <b>Restore a Member</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Send the numeric User ID of the member you'd like to welcome back.</i>\n"
        "<blockquote>Their full access returns instantly and they'll be told it's been restored — no other changes to their wallet or library.</blockquote>")


@router.message(AdminFSM.awaiting_ban_id)
async def do_ban(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if not target.isdigit():
        await message.answer(
            "❌ <b>That doesn't look like a User ID</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>A User ID is digits only — for example <code>123456789</code>. Open 🚫 Ban User again and resend it.</i>")
        return
    await set_ban(int(target), True)
    await log_action(message.chat.id, "ban", target)
    await message.answer(
        "✅ <b>Member Banned</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>User <code>{target}</code> can no longer use Books Provider.</i>\n"
        "<blockquote>They've been notified and access is revoked across the bot. This action is recorded in your audit log.</blockquote>\n"
        "<i>💡 Changed your mind? Lift it any time from ✅ Unban User.</i>")
    try:
        await message.bot.send_message(
            int(target),
            "🔒 <b>Access Paused</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Your access to Books Provider has been suspended by our team.</i>\n"
            "<blockquote>If you think this is a mistake, reach out to support and we'll take a look.</blockquote>")
    except Exception:  # noqa: BLE001
        pass


@router.message(AdminFSM.awaiting_unban_id)
async def do_unban(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if not target.isdigit():
        await message.answer(
            "❌ <b>That doesn't look like a User ID</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>A User ID is digits only — for example <code>123456789</code>. Open ✅ Unban User again and resend it.</i>")
        return
    await set_ban(int(target), False)
    await log_action(message.chat.id, "unban", target)
    await message.answer(
        "✅ <b>Access Restored</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>User <code>{target}</code> is welcome back on Books Provider.</i>\n"
        "<blockquote>Their library, wallet and progress are exactly as they left them. This action is recorded in your audit log.</blockquote>")
    try:
        await message.bot.send_message(
            int(target),
            "✨ <b>Welcome Back</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Your access to Books Provider has been fully restored.</i>\n"
            "<blockquote>Your library, bookmarks and wallet are right where you left them — tap /start to pick up where you stopped.</blockquote>")
    except Exception:  # noqa: BLE001
        pass


# ── manage admins (super admin) ─────────────────────────────────────────────────
# (admin_create is handled in economy.py; requests/broadcast/etc. live in their
#  own routers.)
async def _manage_text() -> str:
    extra = set(await get_extra_admins())
    lines = [
        "🛡 <b>Manage Admins</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<i>Your trusted team and the access each member holds.</i>",
        "<blockquote>",
    ]
    for a in sorted(ADMIN_IDS):
        tag = ("👑 <b>Owner</b> — full control" if a == SUPER_ADMIN_ID
               else ("➕ <b>Added</b> — removable here" if a in extra
                     else "🔧 <b>Env</b> — set in config"))
        lines.append(f"• <code>{a}</code> — {tag}")
    lines.append("</blockquote>")
    lines.append("<i>💡 Promote anyone here. Owner and env admins are fixed in config and can't be removed from this screen.</i>")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_manage")
async def cb_manage(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Only the owner can manage the admin team.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _manage_text(),
        reply_markup=kb([btn("➕ Add Admin", "adm_add", style="success"),
                         btn("➖ Remove Admin", "adm_remove", style="danger")],
                        [btn("🔑 Permissions", "admin_perms", style="primary")],
                        [btn("🔙 Back to Console", "admin_open", style="primary")]))


@router.callback_query(F.data == "adm_add")
async def cb_adm_add(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Only the owner can add admins.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_add_admin)
    await call.message.answer(
        "➕ <b>Add an Admin</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Send the numeric User ID of the member you'd like to bring onto the team.</i>\n"
        "<blockquote>They'll gain access to the admin console and be notified. You can fine-tune exactly what they can do under 🔑 Permissions.</blockquote>\n"
        "<i>💡 Send /cancel to stop.</i>")


@router.callback_query(F.data == "adm_remove")
async def cb_adm_remove(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Only the owner can remove admins.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFSM.awaiting_remove_admin)
    await call.message.answer(
        "➖ <b>Remove an Admin</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Send the numeric User ID of the admin you'd like to step down.</i>\n"
        "<blockquote>They'll lose console access right away. Owner and env admins are fixed in config and can't be removed here.</blockquote>\n"
        "<i>💡 Send /cancel to stop.</i>")


@router.message(AdminFSM.awaiting_add_admin)
async def do_add_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if target.lower() == "/cancel":
        await message.answer(
            "❌ <b>Cancelled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No changes made — your admin team is unchanged.</i>"); return
    if not target.isdigit():
        await message.answer(
            "❌ <b>That doesn't look like a User ID</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>A User ID is digits only — for example <code>123456789</code>. Open ➕ Add Admin again and resend it.</i>"); return
    added = await add_admin(int(target))
    if added:
        await log_action(message.chat.id, "add_admin", target)
    await message.answer(
        ("✨ <b>Admin Added</b>\n"
         "━━━━━━━━━━━━━━━━━━━━\n"
         f"<i>User <code>{target}</code> has joined your team.</i>\n"
         "<blockquote>They now have console access and we've sent them a heads-up. Set exactly what they can do under 🔑 Permissions.</blockquote>") if added
        else ("ℹ️ <b>Already on the Team</b>\n"
              "━━━━━━━━━━━━━━━━━━━━\n"
              f"<i>User <code>{target}</code> is already an admin — nothing changed.</i>"),
        reply_markup=kb([btn("🛡 Manage Admins", "admin_manage", style="primary")]))
    if added:
        try:
            await message.bot.send_message(
                int(target),
                "🛡 <b>You're Now an Admin</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>The owner has invited you onto the Books Provider team.</i>\n"
                "<blockquote>Open /admin to reach your console — your available tools depend on the permissions you've been granted.</blockquote>")
        except Exception:  # noqa: BLE001
            pass


@router.message(AdminFSM.awaiting_remove_admin)
async def do_remove_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    target = (message.text or "").strip()
    if target.lower() == "/cancel":
        await message.answer(
            "❌ <b>Cancelled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No changes made — your admin team is unchanged.</i>"); return
    if not target.isdigit():
        await message.answer(
            "❌ <b>That doesn't look like a User ID</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>A User ID is digits only — for example <code>123456789</code>. Open ➖ Remove Admin again and resend it.</i>"); return
    ok = await remove_admin(int(target))
    if ok:
        await log_action(message.chat.id, "remove_admin", target)
    await message.answer(
        ("✅ <b>Admin Removed</b>\n"
         "━━━━━━━━━━━━━━━━━━━━\n"
         f"<i>User <code>{target}</code> has stepped down and no longer has console access.</i>\n"
         "<blockquote>This change is recorded in your audit log.</blockquote>") if ok else
        ("⚠️ <b>Can't Remove That One</b>\n"
         "━━━━━━━━━━━━━━━━━━━━\n"
         f"<i>User <code>{target}</code> isn't a removable admin.</i>\n"
         "<blockquote>Owner and env admins are fixed in config — change those at the source rather than here.</blockquote>"),
        reply_markup=kb([btn("🛡 Manage Admins", "admin_manage", style="primary")]))
