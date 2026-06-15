"""
handlers/economy.py — wallet, daily claim, redeem codes.

  /claim   — free daily BCN (random 3–5), expires in 24h
  /balance — wallet view (also the "Balance" dashboard button)
  /redeem  — enter a code → credit BGM (one claim per user, limited supply)
  /create  — (admin) mint a redeem code: /create <max_claims> <total_bgm>
"""
import logging
import random
import string
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, BCN_EXPIRY_SECONDS
from database.connection import MongoManager
from utils.keyboards import btn, kb
from utils.wallet import add_bgm, get_balances, seconds_until_claim, set_daily_bcn

logger = logging.getLogger(__name__)
router = Router()


class RedeemFSM(StatesGroup):
    awaiting_code = State()
    cc_max = State()      # create-code: how many claims
    cc_total = State()    # create-code: total BGM to split


async def _mint_code(max_claims: int, total: float, created_by: int) -> tuple[str, float]:
    """Create a redeem code splitting `total` BGM across `max_claims` claims."""
    per = round(total / max_claims, 3)
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    db = await MongoManager.get()
    await db.safe_insert("codes", {
        "code": code, "amount_per_claim": per, "remaining": max_claims,
        "created_by": created_by, "created_at": datetime.now(timezone.utc),
    })
    return code, per


def _fmt_dur(seconds: int) -> str:
    h, m = seconds // 3600, (seconds % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m {seconds % 60}s"


# ── /balance ─────────────────────────────────────────────────────────────────
async def _balance_view(uid: int):
    bgm, bcn = await get_balances(uid)
    left = await seconds_until_claim(uid)
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"ebook_requests": 1, "audiobook_requests": 1,
                                  "downloads": 1}) or {}
    eb = int(u.get("ebook_requests") or 0)
    ab = int(u.get("audiobook_requests") or 0)
    from utils.vip import badge
    vip = await badge(uid)
    claim_line = ("🎁 <b>Daily Bonus:</b> READY — /claim now"
                  if left == 0 else f"🎁 <b>Daily Bonus:</b> in {_fmt_dur(left)}")
    text = (
        "<b>💼 Your Wallet</b>\n"
        + (f"{vip}\n" if vip else "")
        + "━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>BGM:</b> <code>{bgm:.3f}</code>  <i>(permanent)</i>\n"
        f"🪙 <b>BCN:</b> <code>{bcn:.3f}</code>  <i>(expires 24h)</i>\n"
        f"⚖️ <b>Total:</b> <code>{bgm + bcn:.3f}</code>\n\n"
        f"📚 eBook reqs: <code>{eb}</code>  ·  🎧 Audio reqs: <code>{ab}</code>  ·  "
        f"📈 Total: <code>{eb + ab}</code>\n\n"
        f"{claim_line}"
    )
    rows = []
    if left == 0:
        rows.append([btn("⚡ Claim Daily BCN", "do_claim", style="success")])
    rows.append([btn("💎 Buy BGM", "acc_buy", style="success"),
                 btn("🎟 Redeem", "acc_redeem", style="primary")])
    if bcn > 0:
        rows.append([btn("🔄 Convert BCN→BGM", "convert_bcn", style="primary")])
    rows.append([btn("🔙 Back", "menu_account", style="danger")])
    return text, kb(*rows)


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    text, markup = await _balance_view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "acc_balance")
async def cb_balance(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _balance_view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


# ── /claim ───────────────────────────────────────────────────────────────────
async def _do_claim(uid: int) -> tuple[str, object]:
    left = await seconds_until_claim(uid)
    if left > 0:
        return (f"⏳ <b>Claim on cooldown.</b>\nNext claim in <b>{_fmt_dur(left)}</b>.",
                kb([btn("💎 Buy BGM (skip wait)", "acc_buy", style="success")],
                   [btn("🔙 Back", "menu_account", style="danger")]))
    from utils.settings import get_float
    from utils.vip import claim_multiplier
    lo = await get_float("claim_min")
    hi = await get_float("claim_max")
    mult = await claim_multiplier(uid)
    bonus = round(random.uniform(min(lo, hi), max(lo, hi)) * mult, 2)
    await set_daily_bcn(uid, bonus)
    from utils.missions import mark
    await mark(uid, "claim")
    return (f"✨ <b>Claim Successful!</b>\n\n💰 <b>+{bonus:.2f} BCN</b>\n"
            "📅 Valid for 24 hours.",
            kb([btn("💼 View Balance", "acc_balance", style="primary")],
               [btn("🔙 Back", "menu_account", style="danger")]))


@router.message(Command("claim"))
async def cmd_claim(message: Message) -> None:
    text, markup = await _do_claim(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "do_claim")
async def cb_claim(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _do_claim(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


# ── /redeem ──────────────────────────────────────────────────────────────────
@router.message(Command("redeem"))
async def cmd_redeem(message: Message, state: FSMContext) -> None:
    await _prompt_redeem(message, state)


@router.callback_query(F.data == "acc_redeem")
async def cb_redeem(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _prompt_redeem(call.message, state)


async def _prompt_redeem(message: Message, state: FSMContext) -> None:
    await state.set_state(RedeemFSM.awaiting_code)
    await message.answer(
        "🎟 <b>Redeem a Code</b>\n\nType or paste your code below.\n"
        "<i>Codes are case-sensitive and usually one-use per account.</i>",
        reply_markup=kb([btn("❌ Cancel", "menu_account", style="danger")]),
    )


@router.message(RedeemFSM.awaiting_code, F.text)
async def on_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    if code.startswith("/"):
        await state.clear()
        return
    await state.clear()
    uid = message.chat.id
    db = await MongoManager.get()

    doc = await db.find_one_global("codes", {"code": code})
    if not doc:
        await message.answer("❌ <b>Invalid code.</b> Check for typos and try again.")
        return
    amount = float(doc.get("amount_per_claim", 0))

    # 1) Claim the per-user slot first. The unique index on (code,user_id) makes
    #    this the atomic guard against double-tap / concurrent re-redeem.
    claimed = await db.safe_insert("code_claims", {"code": code, "user_id": uid,
                                                   "at": datetime.now(timezone.utc)})
    if not claimed:
        await message.answer("⚠️ You have already redeemed this code.")
        return

    # 2) Atomically decrement remaining only if a unit is left. If none, roll
    #    back the claim so the user can redeem a different (still-stocked) code.
    dec = await db.find_one_and_update_global(
        "codes", {"code": code, "remaining": {"$gt": 0}}, {"$inc": {"remaining": -1}})
    if not dec:
        for idx in db.healthy:
            await db.dbs[idx]["code_claims"].delete_one({"code": code, "user_id": uid})
        await message.answer("❌ <b>Code exhausted.</b> All claims used up.")
        return

    await add_bgm(uid, amount)
    await message.answer(
        f"✨ <b>Redeemed!</b>\n\n🎁 <b>+{amount} BGM</b> added to your wallet.",
        reply_markup=kb([btn("💼 Check Balance", "acc_balance", style="primary")]),
    )


# ── BCN → BGM converter ────────────────────────────────────────────────────────
_CONVERT_TAX = 0.25            # 25% tax → 75% credited
_CONVERT_MIN_BGM = 50.0        # must already hold ≥50 BGM
_CONVERT_MONTHLY_CAP = 10      # uses per calendar month


def _month_key() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.year}-{n.month:02d}"


@router.callback_query(F.data == "convert_bcn")
async def cb_convert(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    bgm, bcn = await get_balances(uid)
    db = await MongoManager.get()
    udoc = await db.find_one_global("users", {"user_id": uid},
                                    {"convert_month": 1, "convert_count": 1}) or {}
    used = udoc.get("convert_count", 0) if udoc.get("convert_month") == _month_key() else 0

    if bcn <= 0:
        await call.message.edit_text("You have no BCN to convert.",
                                     reply_markup=kb([btn("🔙 Back", "acc_balance", style="danger")]))
        return
    if bgm < _CONVERT_MIN_BGM:
        await call.message.edit_text(
            f"🔒 <b>Converter Locked</b>\n\nYou must hold at least <b>{_CONVERT_MIN_BGM:.0f} BGM</b> "
            f"to use the converter.\nYou have {bgm:.2f} BGM.",
            reply_markup=kb([btn("🔙 Back", "acc_balance", style="danger")]))
        return
    if used >= _CONVERT_MONTHLY_CAP:
        await call.message.edit_text(
            f"🔒 Monthly converter limit reached ({_CONVERT_MONTHLY_CAP}×). Try next month.",
            reply_markup=kb([btn("🔙 Back", "acc_balance", style="danger")]))
        return

    credited = round(bcn * (1 - _CONVERT_TAX), 3)
    await call.message.edit_text(
        "<b>🔄 Convert BCN → BGM</b>\n"
        f"🪙 Converting: <code>{bcn:.3f} BCN</code>\n"
        f"🧾 Tax (25%): <code>{bcn * _CONVERT_TAX:.3f}</code>\n"
        f"💎 You receive: <code>{credited:.3f} BGM</code>\n\n"
        f"Uses this month: {used}/{_CONVERT_MONTHLY_CAP}",
        reply_markup=kb([btn("✅ Confirm Convert", "convert_do", style="success")],
                        [btn("🔙 Back", "acc_balance", style="danger")]))


@router.callback_query(F.data == "convert_do")
async def cb_convert_do(call: CallbackQuery) -> None:
    uid = call.from_user.id
    bgm, bcn = await get_balances(uid)
    db = await MongoManager.get()
    udoc = await db.find_one_global("users", {"user_id": uid},
                                    {"convert_month": 1, "convert_count": 1}) or {}
    used = udoc.get("convert_count", 0) if udoc.get("convert_month") == _month_key() else 0
    if bcn <= 0 or bgm < _CONVERT_MIN_BGM or used >= _CONVERT_MONTHLY_CAP:
        await call.answer("Conditions no longer met.", show_alert=True)
        return
    await call.answer()
    # Atomic: flip bookcoin→0 only if it's still >0, returning the OLD value so
    # the credited amount is computed from exactly what we zeroed. A concurrent
    # second tap finds bookcoin already 0 → no match → no double credit.
    old = await db.find_one_and_update_global(
        "users", {"user_id": uid, "bookcoin": {"$gt": 0}},
        {"$set": {"bookcoin": 0.0, "bcn_claimed_at": None,
                  "convert_month": _month_key(), "convert_count": used + 1}},
        return_before=True)
    if not old:
        await call.message.edit_text("Nothing to convert.",
                                     reply_markup=kb([btn("🔙 Back", "acc_balance", style="danger")]))
        return
    credited = round(float(old.get("bookcoin") or 0) * (1 - _CONVERT_TAX), 3)
    await add_bgm(uid, credited)
    text, markup = await _balance_view(uid)
    await call.message.edit_text(f"✅ Converted to <b>{credited:.3f} BGM</b>.\n\n" + text,
                                 reply_markup=markup)


# ── /create (admin) ────────────────────────────────────────────────────────────
@router.message(Command("create"))
async def cmd_create(message: Message, command: CommandObject) -> None:
    if message.chat.id not in ADMIN_IDS:
        await message.answer("🚫 Access denied.")
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or not all(p.replace(".", "").isdigit() for p in parts):
        await message.answer("❌ Usage: <code>/create &lt;max_claims&gt; &lt;total_bgm&gt;</code>\n"
                             "Example: <code>/create 20 50</code> → 2.5 BGM each.")
        return
    max_claims, total = int(parts[0]), float(parts[1])
    if max_claims <= 0 or total <= 0:
        await message.answer("❌ Max claims and total BGM must be > 0.")
        return
    code, per = await _mint_code(max_claims, total, message.chat.id)
    await message.answer(
        "✅ <b>Redeem Code Created</b>\n\n"
        f"🎟️ <code>{code}</code>\n🧮 Claims: {max_claims}\n"
        f"💸 Per user: {per} BGM\n💰 Total: {total:g} BGM")


# ── 🎟️ Create Code (admin panel — interactive) ─────────────────────────────────
@router.callback_query(F.data == "admin_create")
async def cb_admin_create(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(RedeemFSM.cc_max)
    await call.message.answer(
        "🎟️ <b>Create Redeem Code</b>\n\nHow many users can claim it? "
        "Send a whole number. /cancel to abort.")


@router.message(RedeemFSM.cc_max, F.text)
async def cc_max_in(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("❌ Send a whole number greater than 0.")
        return
    await state.update_data(max_claims=int(raw))
    await state.set_state(RedeemFSM.cc_total)
    await message.answer("💰 Total <b>BGM</b> to load into the code (split across claims)? "
                         "e.g. <code>50</code>")


@router.message(RedeemFSM.cc_total, F.text)
async def cc_total_in(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    try:
        total = float(raw)
        if total <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Send a positive number.")
        return
    data = await state.get_data()
    await state.clear()
    max_claims = int(data.get("max_claims", 1))
    code, per = await _mint_code(max_claims, total, message.chat.id)
    await message.answer(
        "✅ <b>Redeem Code Created</b>\n\n"
        f"🎟️ <code>{code}</code>\n🧮 Claims: {max_claims}\n"
        f"💸 Per user: {per} BGM\n💰 Total: {total:g} BGM\n\n"
        "Share the code — users claim it via 🎟 Redeem.",
        reply_markup=kb([btn("🎟️ Create Another", "admin_create", style="success")],
                        [btn("🔙 Admin", "admin_open", style="primary")]))
