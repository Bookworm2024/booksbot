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
from utils.permissions import is_admin, is_super, perms_for
from utils.users import set_ban

logger = logging.getLogger(__name__)
router = Router()


class AdminFSM(StatesGroup):
    awaiting_ban_id = State()
    awaiting_unban_id = State()
    awaiting_add_admin = State()
    awaiting_remove_admin = State()


def _is_admin(uid: int) -> bool:
    return is_admin(uid)


async def _panel_kb(uid: int):
    """Permission-driven panel: a normal admin sees ONLY the tools delegated to
    them; every owner-only control (payments, broadcast, economy, branding,
    settings) is shown to the super admin alone."""
    sup = is_super(uid)
    perms = await perms_for(uid)
    rows = []
    if sup:
        rows.append([webapp_btn("📊 Revenue Dashboard", "admin.html", style="success", fallback_cb="admin_open")])
    if sup or "requests" in perms:
        rows.append([btn("📬 Request Queue", "admin_requests", style="primary")])
    if sup or "ban" in perms:
        rows.append([btn("🚫 Ban User", "admin_ban", style="danger"),
                     btn("✅ Unban User", "admin_unban", style="success")])
    if sup or "moderation" in perms:
        rows.append([btn("🚩 Reports", "admin_reports", style="primary"),
                     btn("🚨 Risk Review", "admin_risk", style="primary")])
    if sup or "content" in perms:
        rows.append([btn("📥 Import Files", "admin_import", style="success"),
                     btn("🎮 Question Bank", "admin_qbank", style="primary")])
    if sup:
        rows.append([btn("📡 Broadcast", "admin_broadcast", style="primary"),
                     btn("🧪 A/B Test", "admin_abtest", style="primary")])
        rows.append([btn("💰 Revenue", "admin_revenue", style="success"),
                     btn("⚙️ Live Pricing", "admin_pricing", style="success")])
        rows.append([btn("🔥 Flash Sale", "admin_deal", style="success"),
                     btn("⭐ Featured Slots", "admin_featured", style="success")])
        rows.append([btn("➕ Add BGM", "admin_addbgm", style="success"),
                     btn("👤 User Lookup", "admin_userinfo", style="primary")])
        rows.append([btn("🎟️ Create Code", "admin_create", style="success"),
                     btn("🎁 Bulk Grant", "admin_bulk", style="success")])
        rows.append([btn("🛠 Maintenance", "admin_maint", style="danger"),
                     btn("🏷 Tag Genres", "admin_tag", style="primary")])
        rows.append([btn("🗂 File Channel", "admin_filechan", style="primary"),
                     btn("🛡 Manage Admins", "admin_manage", style="primary")])
        rows.append([btn("🤖 AI Settings", "admin_ai", style="primary"),
                     btn("🧰 More Tools", "admin_more", style="primary")])
    return kb(*rows)


async def _panel_text(uid: int) -> str:
    if is_super(uid):
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
    perms = await perms_for(uid)
    tools = []
    if "requests" in perms:
        tools.append("📬 <b>Request Queue</b> — fulfil member book requests and send files.")
    if "ban" in perms:
        tools.append("🚫 <b>Ban / Unban</b> — remove or restore a member when needed.")
    if "moderation" in perms:
        tools.append("🚩 <b>Reports &amp; Risk</b> — review reports and flagged accounts.")
    if "content" in perms:
        tools.append("🗂 <b>Content</b> — import book files and manage quiz questions.")
    body = "\n".join(tools) or ("You don't have any tools enabled yet — ask the owner "
                                "to grant you access from 🔑 Permissions.")
    return (
        "🛡 <b>Helper Console</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your delegated toolkit. Payments, broadcasts, pricing and bot settings "
        "stay with the owner.</i>\n"
        f"<blockquote>{body}</blockquote>\n"
        "<i>💡 Need more access? Only the owner can grant it, from 🔑 Permissions.</i>"
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
    await message.answer(await _panel_text(uid), reply_markup=await _panel_kb(uid))


@router.callback_query(F.data == "admin_open")
async def cb_admin_open(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is reserved for the Books Provider team.", show_alert=True)
        return
    await call.answer()
    uid = call.from_user.id
    await call.message.edit_text(await _panel_text(uid),
                                 reply_markup=await _panel_kb(uid))


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
