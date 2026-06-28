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

from config import BCN_EXPIRY_SECONDS
from database.connection import MongoManager
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.permissions import is_super
from utils.settings import get_float
from utils.wallet import (
    add_bcn, add_bgm, drain_bcn, get_balances, seconds_until_claim, set_daily_bcn,
)

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
    claim_line = ("🎁 <b>Daily bonus:</b> <i>ready now</i> — tap below to collect"
                  if left == 0 else f"🎁 <b>Daily bonus:</b> <i>unlocks in {_fmt_dur(left)}</i>")
    text = (
        "💼 <b>Your Wallet</b>\n"
        + (f"{vip}\n" if vip else "")
        + "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A premium statement of everything you hold.</i>\n"
        "<blockquote>"
        f"💎 <b>BGM</b>  <code>{fmt_amount(bgm, 3)}</code>  <i>· permanent, your premium balance</i>\n"
        f"🪙 <b>BCN</b>  <code>{fmt_amount(bcn, 3)}</code>  <i>· free daily, expires in 24h</i>\n"
        f"⚖️ <b>Total</b>  <code>{fmt_amount(bgm + bcn, 3)}</code>  <i>· combined spending power</i>"
        "</blockquote>\n"
        "<blockquote>"
        f"📚 eBook requests  <code>{eb}</code>\n"
        f"🎧 Audiobook requests  <code>{ab}</code>\n"
        f"📈 Lifetime requests  <code>{eb + ab}</code>"
        "</blockquote>\n"
        f"{claim_line}"
    )
    # low-balance upsell — surface a gentle prompt when nearly out of tokens
    if bgm + bcn < 1:
        text += ("\n\n💡 <i>Running low — claim your free daily BCN, spin the wheel, "
                 "or top up BGM and keep your library growing.</i>")
    rows = []
    if left == 0:
        rows.append([btn("⚡ Claim Daily BCN", "do_claim", style="success")])
    rows.append([btn("💎 Top Up BGM", "acc_buy", style="success"),
                 btn("🎟 Redeem a Code", "acc_redeem", style="primary")])
    if bcn > 0:
        rows.append([btn("💱 Convert BCN → BGM", "convert_bcn", style="primary")])
    rows.append([btn("🔙 Back to Account", "menu_account", style="danger")])
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
async def _do_claim(uid: int, bot) -> tuple[str, object]:
    left = await seconds_until_claim(uid)
    if left > 0:
        return ("⏳ <b>Daily Bonus Resting</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>You have already collected today's free BCN. The reward "
                f"recharges once a day — your next claim unlocks in <b>{_fmt_dur(left)}</b>.</blockquote>\n"
                "💡 <i>Can't wait? Top up BGM for instant, permanent balance.</i>",
                kb([btn("💎 Top Up BGM (skip the wait)", "acc_buy", style="success")],
                   [btn("🔙 Back to Account", "menu_account", style="danger")]))
    from utils.settings import get_float
    from utils.vip import claim_multiplier
    lo = await get_float("claim_min")
    hi = await get_float("claim_max")
    mult = await claim_multiplier(uid)
    bonus = round(random.uniform(min(lo, hi), max(lo, hi)) * mult, 2)
    await set_daily_bcn(uid, bonus)
    from utils.missions import mark
    await mark(uid, "claim")
    from utils.logs import log_bcn_claim
    await log_bcn_claim(bot, uid, bonus)
    return ("✨ <b>Daily Bonus Collected</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"🪙 <b>+{bonus:.2f} BCN</b> credited to your wallet\n"
            "⏳ <i>Good for the next 24 hours — spend it before it expires</i>"
            "</blockquote>\n"
            "💡 <i>Come back tomorrow to keep your daily streak alive.</i>",
            kb([btn("💼 View My Wallet", "acc_balance", style="primary")],
               [btn("🔙 Back to Account", "menu_account", style="danger")]))


@router.message(Command("claim"))
async def cmd_claim(message: Message) -> None:
    text, markup = await _do_claim(message.chat.id, message.bot)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "do_claim")
async def cb_claim(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _do_claim(call.from_user.id, call.bot)
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
        "🎟 <b>Redeem a Code</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Turn a code into instant 💎 BGM, credited the moment it clears.</i>\n"
        "<blockquote>"
        "Type or paste your code in the chat below and we'll take it from here.\n"
        "🔑 <b>Codes are case-sensitive</b> — match it exactly\n"
        "👤 Most codes are <b>one claim per account</b>"
        "</blockquote>",
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
        await message.answer(
            "❌ <b>That Code Didn't Match</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>We couldn't find a code matching what you entered. Codes are "
            "case-sensitive, so double-check it character by character and try again.</blockquote>\n"
            "💡 <i>Reopen 🎟 Redeem from your wallet to give it another go.</i>")
        return
    amount = float(doc.get("amount_per_claim", 0))

    # 1) Claim the per-user slot first. The unique index on (code,user_id) makes
    #    this the atomic guard against double-tap / concurrent re-redeem.
    claimed = await db.safe_insert("code_claims", {"code": code, "user_id": uid,
                                                   "at": datetime.now(timezone.utc)})
    if not claimed:
        await message.answer(
            "⚠️ <b>Already Redeemed</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This code is already on your account — most codes are limited to "
            "a single claim per member, so there's nothing more to collect here.</blockquote>\n"
            "💡 <i>Have another code? Open 🎟 Redeem and enter it next.</i>")
        return

    # 2) Atomically decrement remaining only if a unit is left. If none, roll
    #    back the claim so the user can redeem a different (still-stocked) code.
    dec = await db.find_one_and_update_global(
        "codes", {"code": code, "remaining": {"$gt": 0}}, {"$inc": {"remaining": -1}})
    if not dec:
        for idx in db.healthy:
            await db.dbs[idx]["code_claims"].delete_one({"code": code, "user_id": uid})
        await message.answer(
            "❌ <b>Code Fully Claimed</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This one's a valid code, but every available claim has already "
            "been taken. Keep an eye out — fresh codes drop in announcements and events.</blockquote>\n"
            "💡 <i>Watch for our next giveaway to grab the next batch.</i>")
        return

    await add_bgm(uid, amount)
    await message.answer(
        "✨ <b>Code Redeemed</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🎁 <b>+{fmt_amount(amount)} BGM</b> has landed in your wallet\n"
        "💎 <i>Permanent balance — ready to spend whenever you are</i>"
        "</blockquote>\n"
        "💡 <i>Put it to work on a download, a perk, or your next great read.</i>",
        reply_markup=kb([btn("💼 View My Wallet", "acc_balance", style="primary")]),
    )


# ── BCN → BGM converter ────────────────────────────────────────────────────────
# Tax % and min-BGM are admin-editable (utils.settings: convert_tax_pct,
# convert_min_bgm); defaults preserve the spec (25% tax, ≥50 BGM).
_CONVERT_MONTHLY_CAP = 10      # uses per calendar month


def _month_key() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.year}-{n.month:02d}"


async def _convert_params() -> tuple[float, float]:
    """Return (tax_fraction, min_bgm) from live settings, tax clamped to [0, .95]."""
    tax = min(0.95, max(0.0, await get_float("convert_tax_pct") / 100.0))
    return tax, await get_float("convert_min_bgm")


@router.callback_query(F.data == "convert_bcn")
async def cb_convert(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    bgm, bcn = await get_balances(uid)
    db = await MongoManager.get()
    udoc = await db.find_one_global("users", {"user_id": uid},
                                    {"convert_month": 1, "convert_count": 1}) or {}
    used = udoc.get("convert_count", 0) if udoc.get("convert_month") == _month_key() else 0
    tax, min_bgm = await _convert_params()

    if bcn <= 0:
        await call.message.edit_text(
            "🪙 <b>No BCN to Convert</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your BCN balance is empty right now. Claim your free daily bonus "
            "or win more in games, then come back to turn it into permanent 💎 BGM.</blockquote>\n"
            "💡 <i>Tip: BCN expires after 24h — converting locks its value in for good.</i>",
            reply_markup=kb([btn("🔙 Back to Wallet", "acc_balance", style="danger")]))
        return
    if bgm < min_bgm:
        await call.message.edit_text(
            "🔒 <b>Converter Locked</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>The BCN → BGM converter unlocks once you hold a minimum premium "
            f"balance.\n💎 <b>Required:</b> <code>{fmt_amount(min_bgm)} BGM</code>\n"
            f"💼 <b>You hold:</b> <code>{fmt_amount(bgm)} BGM</code></blockquote>\n"
            "💡 <i>Top up to reach the threshold and the converter opens right away.</i>",
            reply_markup=kb([btn("💎 Top Up BGM", "acc_buy", style="success")],
                            [btn("🔙 Back to Wallet", "acc_balance", style="danger")]))
        return
    if used >= _CONVERT_MONTHLY_CAP:
        await call.message.edit_text(
            "🔒 <b>Monthly Limit Reached</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>You've used all of this month's conversions "
            f"(<code>{_CONVERT_MONTHLY_CAP}×</code>). The allowance refreshes at the start "
            "of next month — your BCN keeps working in games and downloads until then.</blockquote>",
            reply_markup=kb([btn("🔙 Back to Wallet", "acc_balance", style="danger")]))
        return

    credited = round(bcn * (1 - tax), 3)
    await call.message.edit_text(
        "💱 <b>Convert BCN → BGM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Lock your free daily coins into permanent premium balance.</i>\n"
        "<blockquote>"
        f"🪙 <b>Converting</b>  <code>{fmt_amount(bcn, 3)} BCN</code>\n"
        f"🧾 <b>Tax ({fmt_amount(tax * 100)}%)</b>  <code>−{fmt_amount(bcn * tax, 3)}</code>\n"
        f"💎 <b>You receive</b>  <code>{fmt_amount(credited, 3)} BGM</code>"
        "</blockquote>\n"
        f"📊 <i>Conversions this month: <code>{used}/{_CONVERT_MONTHLY_CAP}</code></i>\n"
        "💡 <i>This converts your full BCN balance. Confirm to lock it in.</i>",
        reply_markup=kb([btn("✅ Confirm Conversion", "convert_do", style="success")],
                        [btn("🔙 Back to Wallet", "acc_balance", style="danger")]))


@router.callback_query(F.data == "convert_do")
async def cb_convert_do(call: CallbackQuery) -> None:
    uid = call.from_user.id
    bgm, bcn = await get_balances(uid)
    db = await MongoManager.get()
    udoc = await db.find_one_global("users", {"user_id": uid},
                                    {"convert_month": 1, "convert_count": 1}) or {}
    used = udoc.get("convert_count", 0) if udoc.get("convert_month") == _month_key() else 0
    tax, min_bgm = await _convert_params()
    if bcn <= 0 or bgm < min_bgm or used >= _CONVERT_MONTHLY_CAP:
        await call.answer(
            "The conversion conditions have changed since this preview. Reopen the "
            "converter from your wallet to see the latest numbers.", show_alert=True)
        return
    await call.answer()
    # Atomically zero bookcoin across ALL clusters, returning the TOTAL drained so
    # a split BCN balance converts in full and the credited amount matches exactly
    # what we zeroed. A concurrent second tap drains 0 → no double credit.
    drained = await drain_bcn(uid)
    if drained <= 0:
        await call.message.edit_text(
            "🪙 <b>Nothing Left to Convert</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your BCN balance just reached zero, so there's nothing to "
            "convert right now. Claim your daily bonus and it'll be ready again.</blockquote>",
            reply_markup=kb([btn("🔙 Back to Wallet", "acc_balance", style="danger")]))
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"convert_month": _month_key(), "convert_count": used + 1}})
    credited = round(drained * (1 - tax), 3)
    try:
        await add_bgm(uid, credited)
    except Exception:  # noqa: BLE001 — never strand the user's drained BCN
        logger.exception("convert: crediting %.3f BGM failed for %s; restoring %.3f BCN",
                         credited, uid, drained)
        await add_bcn(uid, drained)
        await call.message.edit_text(
            "⚠️ <b>Conversion Hit a Snag</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Something interrupted the conversion, so we safely restored your "
            "full BCN balance — nothing was lost. Please give it another try in a moment.</blockquote>",
            reply_markup=kb([btn("💱 Try Again", "convert_bcn", style="success")],
                            [btn("🔙 Back to Wallet", "acc_balance", style="danger")]))
        return
    text, markup = await _balance_view(uid)
    await call.message.edit_text(
        "✨ <b>Conversion Complete</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>💎 <b>+{fmt_amount(credited, 3)} BGM</b> locked into your permanent "
        "balance — your daily coins now carry forward for good.</blockquote>\n\n" + text,
        reply_markup=markup)


# ── /create (admin) ────────────────────────────────────────────────────────────
@router.message(Command("create"))
async def cmd_create(message: Message, command: CommandObject) -> None:
    if not is_super(message.chat.id):
        await message.answer("🔒 <b>Owner only</b>\n<i>Minting redeem codes is reserved for the super admin.</i>")
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or not all(p.replace(".", "").isdigit() for p in parts):
        await message.answer(
            "🛡 <b>Mint a Redeem Code</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            "<b>Usage</b>  <code>/create &lt;max_claims&gt; &lt;total_bgm&gt;</code>\n"
            "<b>Example</b>  <code>/create 20 50</code>  →  20 claims at <code>2.5 BGM</code> each"
            "</blockquote>\n"
            "💡 <i>The total is split evenly across every claim.</i>")
        return
    max_claims, total = int(parts[0]), float(parts[1])
    if max_claims <= 0 or total <= 0:
        await message.answer("⚠️ <b>Values Out of Range</b>\n<i>Both the claim count and total BGM must be greater than zero.</i>")
        return
    code, per = await _mint_code(max_claims, total, message.chat.id)
    await message.answer(
        "✅ <b>Redeem Code Minted</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🎟️ <b>Code</b>  <code>{code}</code>\n"
        f"🧮 <b>Claims</b>  <code>{max_claims}</code>\n"
        f"💸 <b>Per user</b>  <code>{fmt_amount(per, 3)} BGM</code>\n"
        f"💰 <b>Total loaded</b>  <code>{fmt_amount(total)} BGM</code>"
        "</blockquote>\n"
        "💡 <i>Share the code — members claim it via 🎟 Redeem.</i>")


# ── 🎟️ Create Code (admin panel — interactive) ─────────────────────────────────
@router.callback_query(F.data == "admin_create")
async def cb_admin_create(call: CallbackQuery, state: FSMContext) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RedeemFSM.cc_max)
    await call.message.answer(
        "🛡 <b>Create a Redeem Code</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Step 1 of 2 — set how many members can claim it.</i>\n"
        "<blockquote>Send a whole number for the total claims (e.g. <code>20</code>).\n"
        "Type /cancel anytime to abort.</blockquote>")


@router.message(RedeemFSM.cc_max, F.text)
async def cc_max_in(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled</b>\n<i>No code was created.</i>"); return
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ <b>Invalid Count</b>\n<i>Send a whole number greater than 0 for the claim count.</i>")
        return
    await state.update_data(max_claims=int(raw))
    await state.set_state(RedeemFSM.cc_total)
    await message.answer(
        "🛡 <b>Create a Redeem Code</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Step 2 of 2 — set the reward pool.</i>\n"
        "<blockquote>How much total 💎 <b>BGM</b> should the code hold? It'll be split "
        "evenly across every claim (e.g. <code>50</code>).</blockquote>")


@router.message(RedeemFSM.cc_total, F.text)
async def cc_total_in(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled</b>\n<i>No code was created.</i>"); return
    try:
        total = float(raw)
        if total <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ <b>Invalid Amount</b>\n<i>Send a positive number for the total BGM to load.</i>")
        return
    data = await state.get_data()
    await state.clear()
    max_claims = int(data.get("max_claims", 1))
    code, per = await _mint_code(max_claims, total, message.chat.id)
    await message.answer(
        "✅ <b>Redeem Code Minted</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🎟️ <b>Code</b>  <code>{code}</code>\n"
        f"🧮 <b>Claims</b>  <code>{max_claims}</code>\n"
        f"💸 <b>Per user</b>  <code>{fmt_amount(per, 3)} BGM</code>\n"
        f"💰 <b>Total loaded</b>  <code>{fmt_amount(total)} BGM</code>"
        "</blockquote>\n"
        "💡 <i>Share the code — members claim it via 🎟 Redeem.</i>",
        reply_markup=kb([btn("🎟️ Mint Another Code", "admin_create", style="success")],
                        [btn("🔙 Back to Admin", "admin_open", style="primary")]))
