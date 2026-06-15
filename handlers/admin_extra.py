"""
handlers/admin_extra.py — extra admin governance tools.

Admin panel → 🧰 More Tools:
  🔨 Bulk Ban       — ban many user IDs at once
  📜 Audit Log      — recent admin actions (utils.audit)
  🚩 Feature Flags  — turn features on/off live (utils.flags)
"""
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, SUPER_ADMIN_ID
from utils.audit import log_action, recent
from utils.flags import FLAGS, all_flags, is_on, set_flag
from utils.keyboards import btn, kb
from utils.users import set_ban

logger = logging.getLogger(__name__)
router = Router()


class ExtraFSM(StatesGroup):
    bulk_ban = State()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


@router.callback_query(F.data == "admin_more")
async def cb_more(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🧰 <b>More Admin Tools</b>",
        reply_markup=kb(
            [btn("🔨 Bulk Ban", "admin_bulkban", style="danger")],
            [btn("📜 Audit Log", "admin_audit", style="primary")],
            [btn("🚩 Feature Flags", "admin_flags", style="primary")],
            [btn("🔙 Back", "admin_open", style="primary")]))


# ── bulk ban ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_bulkban")
async def cb_bulkban(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(ExtraFSM.bulk_ban)
    await call.message.answer("🔨 <b>Bulk Ban</b>\n\nSend the User IDs to ban — separated by "
                              "spaces, commas or new lines. /cancel to abort.")


@router.message(ExtraFSM.bulk_ban, F.text)
async def on_bulkban(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    ids = [int(x) for x in re.split(r"[\s,]+", raw) if x.lstrip("-").isdigit()]
    ids = [i for i in dict.fromkeys(ids) if i > 0 and i != SUPER_ADMIN_ID]
    if not ids:
        await message.answer("⚠️ No valid User IDs found.")
        return
    for uid in ids:
        await set_ban(uid, True)
    await log_action(message.chat.id, "bulk_ban", f"{len(ids)} users")
    await message.answer(f"✅ Banned <b>{len(ids)}</b> user(s).",
                         reply_markup=kb([btn("🔙 More Tools", "admin_more", style="primary")]))


# ── audit log ────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_audit")
async def cb_audit(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    rows = await recent(20)
    if not rows:
        body = "<i>No admin actions logged yet.</i>"
    else:
        lines = []
        for r in rows:
            at = r.get("at")
            ts = at.strftime("%d %b %H:%M") if hasattr(at, "strftime") else "—"
            detail = f" · {r['detail']}" if r.get("detail") else ""
            lines.append(f"<code>{ts}</code> · <code>{r.get('admin_id')}</code> · "
                         f"<b>{r.get('action')}</b>{detail}")
        body = "\n".join(lines)
    await call.message.edit_text(
        "📜 <b>Audit Log</b> (last 20)\n━━━━━━━━━━━━━━━━━━\n" + body,
        reply_markup=kb([btn("🔄 Refresh", "admin_audit", style="primary")],
                        [btn("🔙 Back", "admin_more", style="primary")]))


# ── feature flags (super admin) ──────────────────────────────────────────────
@router.callback_query(F.data == "admin_flags")
async def cb_flags(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    flags = await all_flags()
    rows = []
    for key, label in FLAGS.items():
        on = flags.get(key, True)
        rows.append([btn(f"{'🟢' if on else '🔴'} {label}", f"flag:{key}",
                         style="success" if on else "danger")])
    rows.append([btn("🔙 Back", "admin_more", style="primary")])
    await call.message.edit_text(
        "🚩 <b>Feature Flags</b>\nTap to switch a feature on/off — applies instantly.",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("flag:"))
async def cb_flag_toggle(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key not in FLAGS:
        await call.answer("Unknown flag", show_alert=True)
        return
    cur = await is_on(key)
    await set_flag(key, not cur)
    await log_action(call.from_user.id, "flag_toggle", f"{key}={'off' if cur else 'on'}")
    await call.answer(f"{FLAGS[key]} → {'OFF' if cur else 'ON'}")
    await cb_flags(call)
