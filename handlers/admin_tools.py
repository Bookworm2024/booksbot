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

from database.connection import MongoManager
from utils.format import MAX_AMOUNT, fmt_amount, valid_amount
from utils.keyboards import btn, cancel_row, kb
from utils.permissions import is_super
from utils.vip import badge
from utils.wallet import add_bgm, get_balances, set_bgm

logger = logging.getLogger(__name__)
router = Router()


class ToolsFSM(StatesGroup):
    addbgm_user = State()
    addbgm_amount = State()
    lookup = State()
    bulk_amount = State()
    setbgm_amount = State()


def _is_super(uid: int) -> bool:
    return is_super(uid)


# ── Add BGM ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_addbgm")
async def cb_addbgm(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsFSM.addbgm_user)
    await call.message.answer(
        "➕ <b>Grant BGM</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Credit 💎 BGM straight to a member's wallet — they're notified the "
        "moment it lands.\n\n"
        "Send the recipient's <b>User ID</b> to begin.</blockquote>\n"
        "<i>💡 Tap Cancel below to step back.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(ToolsFSM.addbgm_user, F.text)
async def on_addbgm_user(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b>\n<i>No changes were made.</i>"); return
    if not raw.isdigit():
        await message.answer(
            "⚠️ <b>That doesn't look like a User ID</b>\n"
            "<i>A User ID is numbers only — please send just the digits.</i>"); return
    await state.update_data(target=int(raw))
    await state.set_state(ToolsFSM.addbgm_amount)
    await message.answer(
        "💎 <b>Amount to grant</b>\n"
        "<i>How much BGM should land in their wallet? Send the number.</i>")


@router.message(ToolsFSM.addbgm_amount, F.text)
async def on_addbgm_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    ok, amount = valid_amount(raw)
    if not ok:
        await message.answer(
            f"⚠️ <b>That amount won't work</b>\n"
            f"<i>Enter a positive number up to <code>{fmt_amount(MAX_AMOUNT)}</code> — no "
            "<code>1e21</code> or <code>inf</code> values.</i>")
        return
    target = data.get("target")
    await add_bgm(target, amount)
    await message.answer(
        "✨ <b>Grant complete</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>💎 <b>{fmt_amount(amount)} BGM</b> credited to <code>{target}</code>.\n"
        "Their wallet is updated and a notification is on its way.</blockquote>")
    try:
        await message.bot.send_message(
            target,
            "🎁 <b>A gift has landed</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>The team just added <b>+{fmt_amount(amount)} BGM</b> 💎 to your "
            "wallet — yours to spend across the library, games and rewards.</blockquote>\n"
            "<i>💡 Tip: BGM never expires, so there's no rush to use it.</i>")
    except Exception:  # noqa: BLE001
        pass


# ── User lookup ──────────────────────────────────────────────────────────────
@router.message(Command("user"))
async def cmd_user(message: Message, state: FSMContext) -> None:
    if not _is_super(message.chat.id):
        await message.answer("🔒 <b>Owner only</b>\n<i>This tool is reserved for the super admin.</i>"); return
    await state.set_state(ToolsFSM.lookup)
    await message.answer(
        "👤 <b>User Lookup</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Pull a 360° profile — wallet, VIP tier, requests, downloads and "
        "account status, all in one card.\n\n"
        "Send the member's <b>User ID</b> to view it.</blockquote>")


@router.callback_query(F.data == "admin_userinfo")
async def cb_userinfo(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsFSM.lookup)
    await call.message.answer(
        "👤 <b>User Lookup</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Pull a 360° profile — wallet, VIP tier, requests, downloads and "
        "account status, all in one card.\n\n"
        "Send the member's <b>User ID</b> to view it.</blockquote>")


@router.message(ToolsFSM.lookup, F.text)
async def on_lookup(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.clear()
    if not raw.isdigit():
        await message.answer(
            "⚠️ <b>That doesn't look like a User ID</b>\n"
            "<i>A User ID is numbers only — please send just the digits.</i>"); return
    uid = int(raw)
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid})
    if not u:
        await message.answer(
            "🔍 <b>No member found</b>\n"
            "<i>No account matches that ID — double-check the number and try again.</i>"); return
    bgm, bcn = await get_balances(uid)
    vip = await badge(uid) or "—"
    joined = u.get("joined_at")
    joined_s = joined.strftime("%d %b %Y") if hasattr(joined, "strftime") else "—"
    pending = await db.count_global("requests", {"user_id": uid, "status": "pending"})
    fulfilled = await db.count_global("requests", {"user_id": uid, "status": "fulfilled"})
    await message.answer(
        f"👤 <b>Member Profile · {uid}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🧑 <b>{u.get('first_name','—')}</b> (@{u.get('username') or '—'})\n"
        f"🚦 Status: <b>{'🚫 Banned' if u.get('is_banned') else '✅ Active'}</b>\n"
        f"👑 VIP tier: <b>{vip}</b>\n"
        f"📅 Joined: <b>{joined_s}</b></blockquote>\n"
        "<b>💼 Wallet</b>\n"
        "<blockquote>"
        f"💎 BGM: <code>{fmt_amount(bgm)}</code>\n"
        f"🪙 BCN: <code>{fmt_amount(bcn)}</code>\n"
        f"🎮 Earned in games: <code>{fmt_amount(u.get('game_bgm'))}</code> BGM</blockquote>\n"
        "<b>📊 Activity</b>\n"
        "<blockquote>"
        f"📥 Downloads: <code>{int(u.get('downloads') or 0)}</code>\n"
        f"📚 eBook requests: <code>{int(u.get('ebook_requests') or 0)}</code> · "
        f"🎧 Audiobooks: <code>{int(u.get('audiobook_requests') or 0)}</code>\n"
        f"📨 Request queue: ⏳ <code>{pending}</code> pending · ✅ <code>{fulfilled}</code> fulfilled\n"
        f"🎁 Referrals: <code>{int(u.get('ref_count') or 0)}</code></blockquote>\n"
        "<i>💡 Use the tools below to credit or correct this member's balance.</i>",
        reply_markup=kb([btn("➕ Grant BGM", "admin_addbgm", style="success"),
                         btn("✏️ Set BGM", f"admin_setbgm:{uid}", style="primary")]))


# ── Set / fix BGM (repair a corrupted balance) ───────────────────────────────────
@router.callback_query(F.data.startswith("admin_setbgm:"))
async def cb_setbgm(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    try:
        target = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("That target ID isn't valid — please reopen the lookup.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsFSM.setbgm_amount)
    await state.update_data(setbgm_target=target)
    await call.message.answer(
        f"✏️ <b>Set BGM · <code>{target}</code></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Use this to repair a wallet — it <b>overwrites</b> the current balance "
        "with the exact figure you send and collapses any split.\n\n"
        "Send the precise BGM balance to set.</blockquote>\n"
        "<i>⚠️ This replaces the value rather than adding to it. Tap Cancel below to step back.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(ToolsFSM.setbgm_amount, F.text)
async def on_setbgm_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b>\n<i>The balance is unchanged.</i>"); return
    data = await state.get_data()
    await state.clear()
    ok, amount = valid_amount(raw, allow_zero=True)
    if not ok:
        await message.answer(
            f"⚠️ <b>That value won't work</b>\n"
            f"<i>Enter a figure from <code>0</code> to <code>{fmt_amount(MAX_AMOUNT)}</code> — no "
            "<code>1e21</code> or <code>inf</code> values.</i>")
        return
    target = data.get("setbgm_target")
    new_val = await set_bgm(target, amount)
    await message.answer(
        "✅ <b>Balance updated</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>💎 BGM for <code>{target}</code> is now <b>{fmt_amount(new_val)}</b>.\n"
        "The wallet has been overwritten with the exact value you set.</blockquote>")


# ── Bulk BGM grant (to ALL users) ───────────────────────────────────────────────
@router.callback_query(F.data == "admin_bulk")
async def cb_bulk(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsFSM.bulk_amount)
    await call.message.answer(
        "🎁 <b>Bulk Grant</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Reward the whole community at once — every member receives the same "
        "💎 BGM credit. Ideal for milestones, apologies or seasonal goodwill.\n\n"
        "How much BGM should <b>every member</b> receive?</blockquote>\n"
        "<i>You'll confirm before anything is granted. Tap Cancel below to step back.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(ToolsFSM.bulk_amount, F.text)
async def on_bulk_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b>\n<i>No one was credited.</i>"); return
    ok, amount = valid_amount(raw)
    if not ok:
        await message.answer(
            f"⚠️ <b>That amount won't work</b>\n"
            f"<i>Enter a positive number up to <code>{fmt_amount(MAX_AMOUNT)}</code>.</i>"); return
    await state.clear()
    db = await MongoManager.get()
    total = await db.count_global("users")
    await message.answer(
        "⚠️ <b>Confirm bulk grant</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>You're about to credit <b>{fmt_amount(amount)} BGM</b> 💎 to every one "
        f"of your <b>{total}</b> members.\n\n"
        "This is immediate and can't be undone in one step — please confirm.</blockquote>",
        reply_markup=kb([btn("✅ Confirm grant", f"bulk_do:{amount}", style="success")],
                        [btn("❌ Cancel", "admin_open", style="danger")]))


@router.callback_query(F.data.startswith("bulk_do:"))
async def cb_bulk_do(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    ok, amount = valid_amount(call.data.split(":", 1)[1])
    if not ok:
        await call.answer("That amount is no longer valid — please start the grant again.", show_alert=True)
        return
    await call.answer("Crediting every wallet…")
    db = await MongoManager.get()
    affected = 0
    for idx in db.healthy:
        res = await db.dbs[idx]["users"].update_many({}, {"$inc": {"bookgem": amount}})
        affected += res.modified_count
    await call.message.edit_text(
        "✨ <b>Bulk grant complete</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>💎 <b>{fmt_amount(amount)} BGM</b> credited to <b>{affected}</b> members.\n"
        "Every wallet is updated and ready to spend.</blockquote>")


# ── Maintenance mode ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_maint")
async def cb_maint(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    db = await MongoManager.get()
    on = bool(await db.kv_get("maintenance", False))
    await call.message.edit_text(
        "🛠 <b>Maintenance Mode</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Pause the bot for everyone but the team — perfect for deploys, "
        "migrations or a quick tidy-up. Members see a friendly hold message; admins keep "
        "full access.</blockquote>\n"
        f"Status: <b>{'🔴 On — members are paused' if on else '🟢 Off — fully open'}</b>",
        reply_markup=kb(
            [btn("🔴 Turn ON", "maint_on", style="danger") if not on
             else btn("🟢 Turn OFF", "maint_off", style="success")],
            [btn("🔙 Back", "admin_open", style="primary")]))


@router.callback_query(F.data.in_({"maint_on", "maint_off"}))
async def cb_maint_toggle(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    db = await MongoManager.get()
    on = call.data == "maint_on"
    await db.kv_set("maintenance", on)
    await call.answer("Maintenance paused for members." if on else "Bot is live for everyone again.")
    await cb_maint(call)
