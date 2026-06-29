"""
handlers/payments.py — Top up the real-money Wallet (UPI ₹ + crypto $).

Freemium model: these rails fund a stored-value WALLET (wallet_inr / wallet_usd),
not BGM. The wallet then buys Premium and pays per-file overage (see
utils/premium.py + handlers/vip.py).

UPI flow (FamPay receipt auto-verify, no admin step):
  💳 Wallet → Top up via UPI → enter ₹ amount → pay the shown UPI ID/QR →
  submit UTR → the IMAP email monitor reads the FamPay credit email, matches UTR
  + exact amount (±₹2) → auto-credits wallet_inr. The fampay_ledger absorbs
  emails that arrive before/after the UTR; a single atomic flip in
  _confirm_payment guarantees exactly one credit.

Crypto flow (OxaPay gateway):
  🌐 Top up via Crypto → pick a USD pack → OxaPay invoice (USD-priced) →
  HMAC-verified /oxapay-webhook credits wallet_usd once the network confirms.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from aiohttp import web

from config import (
    BOT_PUBLIC_URL, IMAP_PASSWORD, IMAP_USER, OXAPAY_MERCHANT_API_KEY,
    PAYMENT_QR_URL, UPI_ID,
)
from database.connection import MongoManager
from utils.oxapay import (
    MIN_USD_AMOUNT, PAID_STATUSES, POPULAR_COINS, create_invoice, make_order_id,
    verify_webhook,
)
from utils.format import fmt_amount
from utils.keyboards import btn, cancel_row, kb, url_btn, webapp_btn
from utils.wallet import add_money

logger = logging.getLogger(__name__)
router = Router()

# Minimum top-up amounts. ₹ keeps the UPI flow sensible; $ packs sit at/above the
# gateway minimum. Premium itself costs ₹280 / $3, so these comfortably cover it.
_MIN_TOPUP_INR = 50
_USD_PACKS = [5, 10, 25, 50, 100]


class PayFSM(StatesGroup):
    awaiting_amount = State()   # UPI: how many ₹ to top up
    awaiting_utr = State()      # UPI: the transaction reference


def _now():
    return datetime.now(timezone.utc)


# Accept a standard 12-digit bank UTR or a FamPay FMPIB id.
_UTR_OK = re.compile(r'^(?:\d{12}|FMPIB\d+)$', re.IGNORECASE)
_AMOUNT_TOLERANCE_INR = 2.0
# Cosmetic countdown only; the order stays matchable even after this elapses.
_UPI_TTL_SEC = 1200      # 20 min
_CRYPTO_TTL_SEC = 3600   # 60 min (matches the OxaPay invoice lifetime)


def _upi_enabled() -> bool:
    return bool(IMAP_USER and IMAP_PASSWORD)


def _crypto_enabled() -> bool:
    return bool(OXAPAY_MERCHANT_API_KEY and BOT_PUBLIC_URL)


# ── Wallet top-up menu ───────────────────────────────────────────────────────
@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    text, markup = await _buy_view(message.chat.id)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "acc_buy")
async def cb_buy(call: CallbackQuery) -> None:
    await call.answer()
    db = await MongoManager.get()
    # stamp the "cart" so the abandoned-cart nudge can follow up if they don't pay
    await db.safe_update("users", {"user_id": call.from_user.id},
                         {"$set": {"cart_opened_at": _now(), "cart_nudged": False}})
    text, markup = await _buy_view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


async def _buy_view(uid: int):
    from utils.wallet import get_money
    from utils.premium import price_inr, price_usd
    inr, usd = await get_money(uid)
    p_inr, p_usd = await price_inr(), await price_usd()
    text = (
        "💳 <b>Your Wallet</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Stored balance you can spend on Premium and extra downloads.</i>\n"
        "<blockquote>"
        f"🇮🇳 <b>INR balance</b>  <code>₹{fmt_amount(inr, 2)}</code>\n"
        f"💵 <b>USD balance</b>  <code>${fmt_amount(usd, 2)}</code>"
        "</blockquote>\n"
        "<blockquote>"
        f"👑 <b>Premium</b> costs <code>₹{fmt_amount(p_inr)}</code> or <code>${fmt_amount(p_usd)}</code> / month — "
        "buy it straight from your wallet.\n"
        "📥 Out of free downloads? A single extra file is a small wallet charge.</blockquote>\n"
        "<i>💡 Top up with UPI (₹) or crypto ($). Both credit your wallet automatically — "
        "no waiting on us.</i>"
    )
    return text, kb(
        [btn("🏦 Top up with UPI (₹)", "pay_upi", style="success")],
        [btn("🌐 Top up with Crypto ($)", "pay_crypto", style="primary")],
        [btn("👑 Buy Premium", "go_premium", style="success")],
        [btn("🔙 Back", "menu_account", style="danger")],
    )


# ── UPI (email auto-verified) ───────────────────────────────────────────────────
@router.callback_query(F.data == "pay_upi")
async def cb_upi(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if not _upi_enabled():
        await call.message.edit_text(
            "🏦 <b>UPI Is Briefly Offline</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Our UPI verifier is paused for the moment — your wallet is safe either way.</i>\n"
            "<blockquote>"
            "🌐 You can top up instantly with <b>crypto</b> instead, or tap <b>Support</b> "
            "below and we'll sort it out for you.</blockquote>"
            "<i>(Admin: set IMAP_USER + IMAP_PASSWORD to re-enable UPI auto-verify.)</i>",
            reply_markup=kb([btn("🌐 Top up with Crypto", "pay_crypto", style="primary")],
                            [btn("🆘 Support", "menu_support", style="primary")],
                            [btn("🔙 Back", "acc_buy", style="danger")]))
        return
    await state.set_state(PayFSM.awaiting_amount)
    await call.message.edit_text(
        "🏦 <b>UPI Top-Up</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>How much would you like to add to your wallet?</i>\n"
        "<blockquote>"
        f"💰 Enter an amount in <b>₹</b> — minimum <code>₹{_MIN_TOPUP_INR}</code>.\n"
        "Whatever you pay lands in your INR wallet balance, ready for Premium or extra downloads."
        "</blockquote>"
        "<i>💡 Just send a number, or tap Cancel below.</i>",
        reply_markup=kb(cancel_row("acc_buy")))


@router.message(PayFSM.awaiting_amount, F.text)
async def on_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ No problem — top-up cancelled. Your wallet is unchanged.")
        return
    try:
        inr = round(float(raw), 2)
    except ValueError:
        await message.answer(
            f"⚠️ <b>Please send a number.</b>\n<i>Enter how many ₹ to add — at least "
            f"<code>₹{_MIN_TOPUP_INR}</code>.</i>")
        return
    if inr < _MIN_TOPUP_INR:
        await message.answer(
            f"⚠️ <b>A little higher, please.</b>\n<i>The minimum top-up is "
            f"<code>₹{_MIN_TOPUP_INR}</code>.</i>")
        return
    uid = message.chat.id
    order_id = make_order_id(uid)
    db = await MongoManager.get()
    await db.safe_insert("payments", {
        "order_id": order_id, "user_id": uid, "username": message.from_user.username or "",
        "method": "upi", "kind": "wallet_topup", "topup_inr": inr,
        "total_due_inr": inr, "status": "waiting", "submitted_utr": None,
        "created_at": _now(),
        "expires_at": (_now() + timedelta(seconds=_UPI_TTL_SEC)).isoformat(),
    })

    # Preferred: the Secure Payment Portal (Mini App) — UPI QR + in-app UTR submit
    # + live status. Falls back to the in-chat UTR flow when no HTTPS URL is set.
    if BOT_PUBLIC_URL:
        await state.clear()
        await message.answer(
            f"💳 <b>Pay ₹{inr:.2f}</b> to top up your wallet\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Everything happens inside one secure screen — scan, pay, done.</i>\n"
            "<blockquote>"
            "🔒 Tap below to open the <b>Secure Payment Portal</b>. Scan the UPI QR, pay, then "
            "submit your UTR right there. We read your payment receipt and credit your wallet "
            "automatically — no admin, no waiting.</blockquote>",
            reply_markup=kb(
                [webapp_btn("🔒 Open Secure Payment Portal", "pay.html",
                            query=f"order_id={order_id}", style="success")],
                [btn("🔙 Back", "acc_buy", style="danger")]))
        return

    # Fallback: collect the UTR in chat.
    await state.update_data(order_id=order_id, total=inr)
    await state.set_state(PayFSM.awaiting_utr)
    rows = []
    if PAYMENT_QR_URL:
        rows.append([url_btn("📷 View QR Code", PAYMENT_QR_URL)])
    rows.append(cancel_row("acc_buy"))
    await message.answer(
        f"💳 <b>Pay ₹{inr:.2f}</b> to top up your wallet\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>UPI ID:</b> <code>{UPI_ID}</code>\n"
        "<blockquote>"
        f"1️⃣ Send <b>exactly ₹{inr:.2f}</b> to the UPI ID above (or scan the QR).\n"
        "2️⃣ Then paste your <b>UTR / transaction reference</b> back here.</blockquote>"
        "<i>💡 We verify it against your payment receipt and credit your wallet automatically — "
        "usually within 1–2 minutes.</i>",
        reply_markup=kb(*rows))


@router.message(PayFSM.awaiting_utr, F.text)
async def on_utr(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    if txt.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ No problem — top-up cancelled. Your wallet is unchanged.")
        return
    utr = txt.upper()
    if not _UTR_OK.match(utr):
        await message.answer(
            "⚠️ <b>That doesn't look like a valid reference.</b>\n<i>Please paste the "
            "<b>12-digit UTR</b> (or FMPIB id) exactly as shown on your payment receipt.</i>")
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    db = await MongoManager.get()

    if await db.find_one_global("payments", {"submitted_utr": utr, "status": "paid"}):
        await message.answer(
            "❌ <b>That reference is already on file.</b>\n<i>This transaction reference has "
            "been used for a completed payment. If something looks off, tap Support below.</i>",
            reply_markup=kb([btn("🆘 Support", "menu_support", style="primary")]))
        return
    order = await db.find_one_global("payments", {"order_id": order_id})
    if not order or order.get("status") not in ("waiting", "utr_submitted"):
        await state.clear()
        await message.answer(
            "⏳ <b>This checkout has timed out.</b>\n<i>No charge was made. Just tap 💳 Wallet "
            "to start a fresh top-up — it only takes a moment.</i>",
            reply_markup=kb([btn("💳 Top Up Again", "acc_buy", style="success")]))
        return

    await db.safe_update("payments", {"order_id": order_id},
                         {"$set": {"submitted_utr": utr, "status": "utr_submitted"}})
    await state.clear()

    # ledger pre-match: the credit email may have already arrived & be parked
    total = float(order.get("total_due_inr") or 0)
    led = await db.find_one_global("fampay_ledger", {"utr": utr, "status": "unclaimed"})
    if led and abs(float(led.get("amount") or 0) - total) <= _AMOUNT_TOLERANCE_INR:
        order["submitted_utr"] = utr
        await _confirm_payment(order, message.bot, email_txn_id=utr,
                               email_amount_inr=float(led.get("amount") or total))
        await db.safe_update("fampay_ledger", {"utr": utr}, {"$set": {"status": "claimed"}})
        return

    await message.answer(
        "✅ <b>Reference Received</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Thank you — we'll take it from here.</i>\n"
        "<blockquote>"
        "🔎 We're matching your payment against the receipt now. The moment it lands "
        "(usually within 1–2 minutes) your wallet is credited automatically and we'll ping you.</blockquote>"
        "<i>💡 You're free to close this chat — nothing else is needed from you.</i>")


async def _confirm_payment(doc: dict, bot, *, email_txn_id: str = "",
                           email_amount_inr: float = 0.0) -> None:
    """Credit a UPI top-up to wallet_inr exactly once. Called by the email monitor,
    the chat ledger pre-match, and the Mini-App portal. The atomic status flip
    guarantees single crediting. Credits the ACTUAL amount paid (from the receipt)."""
    db = await MongoManager.get()
    flipped = await db.find_one_and_update_global(
        "payments", {"order_id": doc["order_id"], "status": {"$ne": "paid"}},
        {"$set": {"status": "paid", "paid_at": _now(),
                  "email_txn_id": email_txn_id, "email_amount_inr": email_amount_inr}})
    if not flipped:
        return  # already credited
    amount = round(float(email_amount_inr or flipped.get("total_due_inr") or 0), 2)
    if amount <= 0:
        return
    await add_money(flipped["user_id"], "wallet_inr", amount)
    from utils.logs import log_purchase
    await log_purchase(bot, flipped["user_id"], amount, f"₹{fmt_amount(amount, 2)}", "upi")
    await db.safe_update("users", {"user_id": flipped["user_id"]},
                         {"$unset": {"cart_opened_at": ""}}, upsert=False)  # cart completed
    try:
        await bot.send_message(
            flipped["user_id"],
            "✨ <b>Wallet Topped Up</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🇮🇳 <b>+₹{fmt_amount(amount, 2)}</b> added to your wallet.\n"
            f"🧾 <b>Reference:</b> <code>{email_txn_id or flipped.get('submitted_utr','')}</code>\n"
            "<i>💡 Spend it on 👑 Premium or extra downloads — your balance never expires.</i>",
            reply_markup=kb([btn("👑 Buy Premium", "go_premium", style="success")],
                            [btn("💳 My Wallet", "acc_buy", style="primary")]))
    except Exception:  # noqa: BLE001
        pass


# ── crypto (OxaPay) ──────────────────────────────────────────────────────────
@router.callback_query(F.data == "pay_crypto")
async def cb_crypto(call: CallbackQuery) -> None:
    await call.answer()
    if not _crypto_enabled():
        await call.message.edit_text(
            "🌐 <b>Crypto Isn't Live Yet</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>This top-up route is being switched on — please use UPI for now, or check "
            "back shortly.</i>\n"
            "<i>(Admin: set OXAPAY_MERCHANT_API_KEY and BOT_PUBLIC_URL to enable crypto.)</i>",
            reply_markup=kb([btn("🔙 Back", "acc_buy", style="danger")]))
        return
    rows = [[btn(f"💰 Top up ${u}", f"cm_buy:{u}", style="success")] for u in _USD_PACKS]
    rows.append([btn("🔙 Back", "acc_buy", style="danger")])
    await call.message.edit_text(
        "🌐 <b>Top up with Crypto</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Fast, borderless, and credited the moment the network confirms.</i>\n"
        "<blockquote>"
        f"💵 Choose how much <b>USD</b> to add · gateway minimum <code>${fmt_amount(MIN_USD_AMOUNT)}</code>\n"
        f"💱 <b>Pay with:</b> {POPULAR_COINS}\n"
        "🔒 Powered by <b>OxaPay</b> — an independent, no-KYC gateway.</blockquote>"
        "<i>💡 Pick an amount below and you'll get a secure pay page where you choose your coin. "
        "Your USD wallet is credited automatically once the payment confirms on-chain.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("cm_buy:"))
async def cb_cm_buy(call: CallbackQuery) -> None:
    try:
        usd = float(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("That selection didn't go through — please tap an amount again.", show_alert=True)
        return
    if usd < MIN_USD_AMOUNT:
        await call.answer("That amount is below the gateway minimum — please pick a larger one.", show_alert=True)
        return
    await call.answer("Preparing your secure invoice…")
    uid = call.from_user.id
    order_id = make_order_id(uid)
    webhook_url = f"{BOT_PUBLIC_URL}/oxapay-webhook"
    result = await create_invoice(order_id, usd, webhook_url)
    if not result or not result.get("url"):
        await call.message.edit_text(
            "⚠️ <b>The Gateway Didn't Respond</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>We couldn't reach the payment gateway just now — nothing was charged. "
            "Please give it a moment and try again.</i>",
            reply_markup=kb([btn("🔄 Try Again", "pay_crypto", style="danger")]))
        return
    db = await MongoManager.get()
    pay_url = result.get("url")
    await db.safe_insert("crypto_orders", {
        "order_id": order_id, "user_id": uid, "kind": "wallet_topup",
        "topup_usd": usd, "amount_usd": usd, "gateway": "oxapay", "method": "crypto",
        "track_id": result.get("track_id"), "pay_url": pay_url or "",
        "status": "waiting", "created_at": _now(),
        "expires_at": (_now() + timedelta(seconds=_CRYPTO_TTL_SEC)).isoformat(),
    })

    if BOT_PUBLIC_URL:
        await call.message.edit_text(
            f"🌐 <b>Top up ${usd:.2f}</b> in crypto\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>One secure screen for everything — pick your coin, pay, watch it confirm.</i>\n"
            "<blockquote>"
            "🔒 Open the <b>Secure Payment Portal</b> to choose your coin and complete payment. "
            "Your USD wallet is credited automatically the moment the network confirms — you'll "
            "see it update live.</blockquote>",
            reply_markup=kb(
                [webapp_btn("🔒 Open Secure Payment Portal", "pay.html",
                            query=f"order_id={order_id}", style="success")],
                [btn("🔙 Back", "acc_buy", style="danger")]))
        return

    rows = [[url_btn("💳 Open Pay Page", pay_url, style="success")],
            [btn("🔙 Back", "acc_buy", style="danger")]]
    await call.message.edit_text(
        f"🌐 <b>Top up ${usd:.2f}</b> in crypto\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Choose your coin on the secure OxaPay page and you're set.</i>\n"
        "<blockquote>"
        "💳 Tap to open the pay page (valid for about <code>60 min</code>) and pick your coin. "
        "Your USD wallet is credited automatically once the payment confirms on-chain.</blockquote>",
        reply_markup=kb(*rows))


# ── OxaPay webhook (registered in bot.py at /oxapay-webhook) ────────────────────
async def oxapay_webhook(request: web.Request) -> web.Response:
    raw = await request.read()
    if not verify_webhook(raw, request.headers.get("HMAC", "")):
        logger.warning("OxaPay webhook failed signature check")
        return web.Response(status=403, text="bad signature")
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.Response(status=400, text="bad json")

    order_id = str(data.get("order_id") or "")
    status = str(data.get("status") or "").lower()
    if not order_id or status not in PAID_STATUSES:
        return web.Response(text="ok")

    db = await MongoManager.get()
    # Atomic flip: only the FIRST callback that flips → paid credits the wallet.
    order = await db.find_one_and_update_global(
        "crypto_orders",
        {"order_id": order_id, "status": {"$ne": "paid"}},
        {"$set": {"status": "paid", "paid_at": _now()}})
    if not order:
        return web.Response(text="ok")  # unknown / already credited (idempotent)
    usd = round(float(order.get("amount_usd") or order.get("topup_usd") or 0), 2)
    if usd <= 0:
        return web.Response(text="ok")
    await add_money(order["user_id"], "wallet_usd", usd)
    bot = request.app["bot"]
    from utils.logs import log_purchase
    await log_purchase(bot, order["user_id"], usd, f"${fmt_amount(usd, 2)}", "crypto")
    await db.safe_update("users", {"user_id": order["user_id"]},
                         {"$unset": {"cart_opened_at": ""}}, upsert=False)  # cart completed
    try:
        await bot.send_message(
            order["user_id"],
            "✨ <b>Crypto Payment Confirmed</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>+${fmt_amount(usd, 2)}</b> added to your wallet.\n"
            "<i>💡 Spend it on 👑 Premium or extra downloads — your balance never expires.</i>",
            reply_markup=kb([btn("👑 Buy Premium", "go_premium", style="success")],
                            [btn("💳 My Wallet", "acc_buy", style="primary")]))
    except Exception:  # noqa: BLE001
        pass
    return web.Response(text="ok")
