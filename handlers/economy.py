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
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import BCN_EXPIRY_SECONDS
from database.connection import MongoManager
from utils.format import fmt_amount
from utils.keyboards import btn, cancel_row, kb
from utils.permissions import is_super
from utils.settings import get_float
from utils.wallet import add_bgm, get_balances, get_money, seconds_until_claim

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
    bgm, _ = await get_balances(uid)
    inr, usd = await get_money(uid)
    left = await seconds_until_claim(uid)
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"ebook_requests": 1, "audiobook_requests": 1,
                                  "downloads": 1}) or {}
    eb = int(u.get("ebook_requests") or 0)
    ab = int(u.get("audiobook_requests") or 0)
    from utils.vip import badge
    vip = await badge(uid)
    claim_line = ("🎁 <b>Daily reward:</b> <i>ready now</i> — tap below to collect"
                  if left == 0 else f"🎁 <b>Daily reward:</b> <i>unlocks in {_fmt_dur(left)}</i>")
    text = (
        "💼 <b>Your Wallet</b>\n"
        + (f"{vip} member\n" if vip else "")
        + "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Everything you hold, in one place.</i>\n"
        "<blockquote>"
        f"💎 <b>BGM</b>  <code>{fmt_amount(bgm, 2)}</code>  <i>· earned in games, referrals &amp; rewards — redeem for Premium</i>\n"
        f"🇮🇳 <b>Wallet ₹</b>  <code>{fmt_amount(inr, 2)}</code>\n"
        f"💵 <b>Wallet $</b>  <code>{fmt_amount(usd, 2)}</code>"
        "</blockquote>\n"
        "<blockquote>"
        f"📚 eBook requests  <code>{eb}</code>\n"
        f"🎧 Audiobook requests  <code>{ab}</code>\n"
        f"📈 Lifetime requests  <code>{eb + ab}</code>"
        "</blockquote>\n"
        f"{claim_line}"
    )
    rows = []
    if left == 0:
        rows.append([btn("⚡ Claim Daily BGM", "do_claim", style="success")])
    rows.append([btn("👑 Get Premium", "go_premium", style="success")])
    rows.append([btn("💳 Top Up Wallet", "acc_buy", style="primary"),
                 btn("🎟 Redeem a Code", "acc_redeem", style="primary")])
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
def _resting_card(left: int):
    return ("⏳ <b>Daily Reward Resting</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>You have already collected today's BGM. The reward "
            f"recharges once a day — your next claim unlocks in <b>{_fmt_dur(left)}</b>.</blockquote>\n"
            "💡 <i>Premium members earn 2× the daily reward.</i>",
            kb([btn("👑 Get Premium", "go_premium", style="success")],
               [btn("🔙 Back to Account", "menu_account", style="danger")]))


async def _do_claim(uid: int, bot) -> tuple[str, object]:
    left = await seconds_until_claim(uid)
    if left > 0:
        return _resting_card(left)
    from utils.settings import get_float
    from utils.vip import claim_multiplier
    # Atomically claim the cooldown FIRST: only the op that flips last_claim_at past
    # the 24h window proceeds to credit, so a double-tap can't double-claim.
    db = await MongoManager.get()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=BCN_EXPIRY_SECONDS)
    claimed = await db.find_one_and_update_global(
        "users",
        {"user_id": uid, "$or": [{"last_claim_at": {"$lt": cutoff}},
                                 {"last_claim_at": None},
                                 {"last_claim_at": {"$exists": False}}]},
        {"$set": {"last_claim_at": now}})
    if not claimed:
        return _resting_card(await seconds_until_claim(uid) or BCN_EXPIRY_SECONDS)
    lo = await get_float("claim_min")
    hi = await get_float("claim_max")
    mult = await claim_multiplier(uid)
    bonus = round(random.uniform(min(lo, hi), max(lo, hi)) * mult, 2)
    await add_bgm(uid, bonus)
    from utils.missions import mark
    await mark(uid, "claim")
    try:
        from utils.logs import log_bcn_claim
        await log_bcn_claim(bot, uid, bonus)
    except Exception:  # noqa: BLE001
        pass
    return ("✨ <b>Daily Reward Collected</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"💎 <b>+{bonus:.2f} BGM</b> credited to your wallet\n"
            "♾️ <i>BGM never expires — save it up to redeem Premium</i>"
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
        "💡 <i>Tap Cancel below to step back.</i></blockquote>",
        reply_markup=kb(cancel_row("menu_account")))


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
