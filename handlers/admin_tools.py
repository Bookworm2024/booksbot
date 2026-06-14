"""
handlers/admin_tools.py — admin power tools.

  ➕ Add BGM       — grant BGM to a user (id → amount → credit + notify)
  👤 User Lookup   — 360° profile (balance, VIP, requests, downloads, ban, joined)
  🛠 Maintenance   — toggle maintenance mode (blocks non-admins, set via kv)
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from database.connection import MongoManager
from utils.keyboards import btn, kb
from utils.vip import badge
from utils.wallet import add_bgm, get_balances

logger = logging.getLogger(__name__)
router = Router()


class ToolsFSM(StatesGroup):
    addbgm_user = State()
    addbgm_amount = State()
    lookup = State()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ── Add BGM ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_addbgm")
async def cb_addbgm(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsFSM.addbgm_user)
    await call.message.answer("➕ <b>Add BGM</b>\nSend the target <b>User ID</b>. /cancel to abort.")


@router.message(ToolsFSM.addbgm_user, F.text)
async def on_addbgm_user(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    if not raw.isdigit():
        await message.answer("⚠️ Send a numeric User ID."); return
    await state.update_data(target=int(raw))
    await state.set_state(ToolsFSM.addbgm_amount)
    await message.answer("💎 How much BGM to add?")


@router.message(ToolsFSM.addbgm_amount, F.text)
async def on_addbgm_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    try:
        amount = round(float(raw), 3)
    except ValueError:
        await message.answer("⚠️ Enter a number."); return
    target = data.get("target")
    await add_bgm(target, amount)
    await message.answer(f"✅ Added <b>{amount:g} BGM</b> to <code>{target}</code>.")
    try:
        await message.bot.send_message(
            target, f"🎁 An admin granted you <b>+{amount:g} BGM</b>.")
    except Exception:  # noqa: BLE001
        pass


# ── User lookup ──────────────────────────────────────────────────────────────
@router.message(Command("user"))
async def cmd_user(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.chat.id):
        await message.answer("🚫 Access denied."); return
    await state.set_state(ToolsFSM.lookup)
    await message.answer("👤 Send the <b>User ID</b> to look up:")


@router.callback_query(F.data == "admin_userinfo")
async def cb_userinfo(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsFSM.lookup)
    await call.message.answer("👤 Send the <b>User ID</b> to look up:")


@router.message(ToolsFSM.lookup, F.text)
async def on_lookup(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.clear()
    if not raw.isdigit():
        await message.answer("⚠️ Send a numeric User ID."); return
    uid = int(raw)
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid})
    if not u:
        await message.answer("❌ No such user."); return
    bgm, bcn = await get_balances(uid)
    vip = await badge(uid) or "—"
    joined = u.get("joined_at")
    joined_s = joined.strftime("%d %b %Y") if hasattr(joined, "strftime") else "—"
    pending = await db.count_global("requests", {"user_id": uid, "status": "pending"})
    fulfilled = await db.count_global("requests", {"user_id": uid, "status": "fulfilled"})
    await message.answer(
        f"👤 <b>User {uid}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🧑 {u.get('first_name','—')} (@{u.get('username') or '—'})\n"
        f"🚦 {'🚫 BANNED' if u.get('is_banned') else '✅ Active'}\n"
        f"👑 VIP: {vip}\n"
        f"💎 BGM: <code>{bgm:.2f}</code> · 🪙 BCN: <code>{bcn:.2f}</code>\n"
        f"📥 Downloads: <code>{int(u.get('downloads') or 0)}</code>\n"
        f"📚 eBook reqs: <code>{int(u.get('ebook_requests') or 0)}</code> · "
        f"🎧 Audio: <code>{int(u.get('audiobook_requests') or 0)}</code>\n"
        f"📨 Requests: ⏳{pending} · ✅{fulfilled}\n"
        f"🎁 Referrals: <code>{int(u.get('ref_count') or 0)}</code>\n"
        f"🎮 Game BGM: <code>{float(u.get('game_bgm') or 0):.2f}</code>\n"
        f"📅 Joined: {joined_s}",
        reply_markup=kb([btn("➕ Add BGM", "admin_addbgm", style="success")]))


# ── Maintenance mode ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_maint")
async def cb_maint(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    db = await MongoManager.get()
    on = bool(await db.kv_get("maintenance", False))
    await call.message.edit_text(
        f"🛠 <b>Maintenance Mode</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Status: <b>{'🔴 ON (users blocked)' if on else '🟢 OFF'}</b>",
        reply_markup=kb(
            [btn("🔴 Turn ON", "maint_on", style="danger") if not on
             else btn("🟢 Turn OFF", "maint_off", style="success")],
            [btn("🔙 Back", "admin_open", style="primary")]))


@router.callback_query(F.data.in_({"maint_on", "maint_off"}))
async def cb_maint_toggle(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    db = await MongoManager.get()
    await db.kv_set("maintenance", call.data == "maint_on")
    await call.answer("Updated")
    await cb_maint(call)
