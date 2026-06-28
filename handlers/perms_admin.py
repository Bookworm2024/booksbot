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
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    rows = []
    for a in sorted(config.ADMIN_IDS):
        if a == SUPER_ADMIN_ID:
            rows.append([btn(f"👑 {a} — Super admin · full access", "perm_super", style="primary")])
        else:
            rows.append([btn(f"🔑 Manage admin {a}", f"perm_pick:{a}", style="primary")])
    rows.append([btn("🔙 Manage Admins", "admin_manage", style="primary")])
    await call.message.edit_text(
        "🛡 <b>Admin Permissions</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Decide exactly what each member of your team can touch.</i>\n\n"
        "<blockquote>Choose an admin below to fine-tune their capabilities — "
        "broadcasts, pricing, the file channel and more — one switch at a time.\n\n"
        "👑 The <b>super admin</b> always holds every key.\n"
        "🔑 A standard admin with nothing turned off keeps <b>full access</b> "
        "by default, so existing helpers keep working until you narrow them.</blockquote>\n"
        "<i>💡 Tip: grant the least you need, then add more as trust grows.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data == "perm_super")
async def cb_super(call: CallbackQuery) -> None:
    await call.answer("The super admin holds every key — nothing to adjust here.", show_alert=True)


async def _pick_view(uid: int):
    held = await perms_for(uid)
    rows = []
    for key, label in PERMS.items():
        on = key in held
        rows.append([btn(f"{'🟢' if on else '🔴'} {label}", f"perm_tog:{uid}:{key}",
                         style="success" if on else "danger")])
    rows.append([btn("🔙 Admins", "admin_perms", style="primary")])
    return (f"🔑 <b>Permissions · admin</b> <code>{uid}</code>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Tap any capability to grant or revoke it instantly.</i>\n\n"
            "<blockquote>🟢 <b>Granted</b> — this admin can use the tool.\n"
            "🔴 <b>Revoked</b> — the tool stays out of reach.\n\n"
            "Changes save the moment you tap and apply on their next "
            "action.</blockquote>"), kb(*rows)


@router.callback_query(F.data.startswith("perm_pick:"))
async def cb_pick(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
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
        await call.answer("This control is reserved for the super admin.", show_alert=True)
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
        await call.answer("The super admin can't be restricted — they hold every key.", show_alert=True)
        return
    now_on = await toggle(uid, key)
    await log_action(call.from_user.id, "perm", f"{uid} {key}={'on' if now_on else 'off'}")
    await call.answer(f"{key} · {'granted ✅' if now_on else 'revoked 🔒'}")
    text, markup = await _pick_view(uid)
    await call.message.edit_text(text, reply_markup=markup)
