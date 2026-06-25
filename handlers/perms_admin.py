"""
handlers/perms_admin.py — granular per-admin permissions panel (super admin).

Admin → 🛡 Manage Admins → 🔑 Permissions → pick an admin → toggle their
capabilities. Backward-compatible: an unrestricted admin keeps full access until
the super admin turns something off. The super admin always has everything.
"""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

import config
from config import SUPER_ADMIN_ID
from utils.audit import log_action
from utils.keyboards import btn, kb
from utils.permissions import PERMS, perms_for, toggle

logger = logging.getLogger(__name__)
router = Router()


def _super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


@router.callback_query(F.data == "admin_perms")
async def cb_perms(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    rows = []
    for a in sorted(config.ADMIN_IDS):
        if a == SUPER_ADMIN_ID:
            rows.append([btn(f"👑 {a} — super (all)", "perm_super", style="primary")])
        else:
            rows.append([btn(f"🔑 {a}", f"perm_pick:{a}", style="primary")])
    rows.append([btn("🔙 Manage Admins", "admin_manage", style="primary")])
    await call.message.edit_text(
        "🔑 <b>Admin Permissions</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Pick an admin to grant/revoke capabilities. Unrestricted admins have "
        "full access by default.", reply_markup=kb(*rows))


@router.callback_query(F.data == "perm_super")
async def cb_super(call: CallbackQuery) -> None:
    await call.answer("The super admin always has every permission.", show_alert=True)


async def _pick_view(uid: int):
    held = await perms_for(uid)
    rows = []
    for key, label in PERMS.items():
        on = key in held
        rows.append([btn(f"{'🟢' if on else '🔴'} {label}", f"perm_tog:{uid}:{key}",
                         style="success" if on else "danger")])
    rows.append([btn("🔙 Admins", "admin_perms", style="primary")])
    return (f"🔑 <b>Permissions for</b> <code>{uid}</code>\n"
            "━━━━━━━━━━━━━━━━━━\nTap to grant/revoke:"), kb(*rows)


@router.callback_query(F.data.startswith("perm_pick:"))
async def cb_pick(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    try:
        uid = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    text, markup = await _pick_view(uid)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("perm_tog:"))
async def cb_tog(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer(); return
    _, uid_s, key = parts
    try:
        uid = int(uid_s)
    except ValueError:
        await call.answer(); return
    if uid == SUPER_ADMIN_ID:
        await call.answer("Can't restrict the super admin.", show_alert=True)
        return
    now_on = await toggle(uid, key)
    await log_action(call.from_user.id, "perm", f"{uid} {key}={'on' if now_on else 'off'}")
    await call.answer(f"{key}: {'ON' if now_on else 'OFF'}")
    text, markup = await _pick_view(uid)
    await call.message.edit_text(text, reply_markup=markup)
