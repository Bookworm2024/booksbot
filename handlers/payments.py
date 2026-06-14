"""
handlers/payments.py — Buy BGM (UPI manual + crypto via Heleket).

UPI flow (no external dependency):
  💎 Buy BGM → 💳 UPI → shows UPI ID + QR + pricing → ✅ Paid →
  enter UTR (validated + de-duplicated) → upload screenshot →
  admins get the proof with ✅ Approve / ❌ Decline →
  Approve → admin enters BGM amount → credited + user notified.

Crypto flow (Heleket gateway, same as inflowads):
  🌐 Crypto → pick coin/network → pick a USD pack (≥$5 gateway min) →
  Heleket invoice → pay page/address → HMAC-verified /heleket-webhook credits
  BGM automatically. Activates when HELEKET_API_KEY + HELEKET_MERCHANT_ID are set.
"""
import logging
import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from aiohttp import web

from config import (
    ADMIN_IDS, BGM_PRICE_INR, BGM_PRICE_USD, BOT_PUBLIC_URL, HELEKET_API_KEY,
    HELEKET_MERCHANT_ID, IMAP_PASSWORD, IMAP_USER, MIN_BGM_PURCHASE,
    PAYMENT_QR_URL, UPI_ID,
)
from database.connection import MongoManager
from utils.heleket import (
    CRYPTO_CHOICES, MIN_USD_AMOUNT, PAID_STATUSES, create_invoice, make_order_id,
    verify_webhook,
)
from utils.keyboards import btn, kb, url_btn
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()


class PayFSM(StatesGroup):
    awaiting_amount = State()   # UPI: how many BGM
    awaiting_utr = State()      # UPI: the transaction reference


def _now():
    return datetime.now(timezone.utc)


# Accept a standard 12-digit bank UTR or a FamPay FMPIB id.
_UTR_OK = re.compile(r'^(?:\d{12}|FMPIB\d+)$', re.IGNORECASE)
_AMOUNT_TOLERANCE_INR = 2.0


def _upi_enabled() -> bool:
    return bool(IMAP_USER and IMAP_PASSWORD)


# ── Buy menu ─────────────────────────────────────────────────────────────────
@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    await message.answer(*_buy_view())


@router.callback_query(F.data == "acc_buy")
async def cb_buy(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = _buy_view()
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


def _buy_view():
    min_inr = int(MIN_BGM_PURCHASE * BGM_PRICE_INR)
    text = (
        "<b>💎 Buy BookGems (BGM)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Permanent tokens — never expire.\n\n"
        f"🏦 <b>UPI (INR):</b> ₹{BGM_PRICE_INR:g}/BGM · min {MIN_BGM_PURCHASE} (₹{min_inr})\n"
        f"🌐 <b>Crypto:</b> ${BGM_PRICE_USD:g}/BGM\n\n"
        "<i>Both auto-credit — UPI is verified from the payment receipt, crypto "
        "on-chain.</i>"
    )
    return text, kb(
        [btn("💳 Pay via UPI (INR)", "pay_upi", style="success")],
        [btn("🌐 Pay via Crypto", "pay_crypto", style="primary")],
        [btn("🔙 Back", "menu_account", style="danger")],
    )


# ── UPI (email auto-verified, like inflowads) ───────────────────────────────────
@router.callback_query(F.data == "pay_upi")
async def cb_upi(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    if not _upi_enabled():
        await call.message.edit_text(
            "💳 <b>UPI is temporarily unavailable.</b>\nPlease use crypto, or "
            "reach out via /support.\n<i>(Admin: set IMAP_USER + IMAP_PASSWORD.)</i>",
            reply_markup=kb([btn("🌐 Pay via Crypto", "pay_crypto", style="primary")],
                            [btn("🔙 Back", "acc_buy", style="danger")]))
        return
    await state.set_state(PayFSM.awaiting_amount)
    min_inr = int(MIN_BGM_PURCHASE * BGM_PRICE_INR)
    await call.message.edit_text(
        "<b>💳 UPI Payment</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"How many <b>BGM</b> do you want? (min {MIN_BGM_PURCHASE} = ₹{min_inr})\n\n"
        "Send a number — /cancel to abort.")


@router.message(PayFSM.awaiting_amount, F.text)
async def on_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    if not raw.isdigit() or int(raw) < MIN_BGM_PURCHASE:
        await message.answer(f"⚠️ Enter a whole number ≥ {MIN_BGM_PURCHASE}.")
        return
    bgm = int(raw)
    inr = round(bgm * BGM_PRICE_INR, 2)
    uid = message.chat.id
    order_id = make_order_id(uid)
    db = await MongoManager.get()
    await db.safe_insert("payments", {
        "order_id": order_id, "user_id": uid, "username": message.from_user.username or "",
        "method": "upi", "bgm": bgm, "total_due_inr": inr, "status": "waiting",
        "submitted_utr": None, "created_at": _now(),
    })
    await state.update_data(order_id=order_id, total=inr)
    await state.set_state(PayFSM.awaiting_utr)
    rows = []
    if PAYMENT_QR_URL:
        rows.append([url_btn("📷 View QR", PAYMENT_QR_URL)])
    await message.answer(
        f"<b>💳 Pay ₹{inr:.2f}</b> for <b>{bgm} BGM</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>UPI ID:</b> <code>{UPI_ID}</code>\n\n"
        f"1️⃣ Send <b>exactly ₹{inr:.2f}</b> to the UPI ID (or scan the QR).\n"
        "2️⃣ Then send your <b>UTR / transaction reference</b> here.\n\n"
        "<i>You'll be credited automatically — usually within 1–2 minutes.</i>",
        reply_markup=kb(*rows) if rows else None)


@router.message(PayFSM.awaiting_utr, F.text)
async def on_utr(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    if txt.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    utr = txt.upper()
    if not _UTR_OK.match(utr):
        await message.answer("⚠️ Send a valid 12-digit UTR (or FMPIB id) from your receipt.")
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    db = await MongoManager.get()

    # reject a UTR already consumed by a confirmed payment
    if await db.find_one_global("payments", {"submitted_utr": utr, "status": "paid"}):
        await message.answer("❌ This transaction reference was already used.")
        return
    order = await db.find_one_global("payments", {"order_id": order_id})
    if not order or order.get("status") not in ("waiting", "utr_submitted"):
        await state.clear()
        await message.answer("⚠️ Session expired — start the purchase again via 💎 Buy BGM.")
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
        "✅ <b>Reference recorded.</b>\nWe'll credit your BGM automatically the moment "
        "the payment lands (usually 1–2 min). You can close this chat.")


async def _confirm_payment(doc: dict, bot, *, email_txn_id: str = "",
                           email_amount_inr: float = 0.0) -> None:
    """Credit a UPI payment exactly once. Called by the email monitor and the
    ledger pre-match. The atomic status flip guarantees single crediting."""
    db = await MongoManager.get()
    flipped = await db.find_one_and_update_global(
        "payments", {"order_id": doc["order_id"], "status": {"$ne": "paid"}},
        {"$set": {"status": "paid", "paid_at": _now(),
                  "email_txn_id": email_txn_id, "email_amount_inr": email_amount_inr}})
    if not flipped:
        return  # already credited
    bgm = float(flipped.get("bgm") or 0)
    await add_bgm(flipped["user_id"], bgm)
    try:
        await bot.send_message(
            flipped["user_id"],
            f"🎉 <b>Payment confirmed!</b>\n💎 <b>+{bgm:g} BGM</b> added to your wallet.\n"
            f"🧾 Ref: <code>{email_txn_id or flipped.get('submitted_utr','')}</code>")
    except Exception:  # noqa: BLE001
        pass


# ── crypto (Heleket) ─────────────────────────────────────────────────────────
# Heleket enforces a ~$5 minimum, so packs are USD-denominated (BGM shown too).
_USD_PACKS = [5, 10, 25, 50]


def _crypto_enabled() -> bool:
    return bool(HELEKET_API_KEY and HELEKET_MERCHANT_ID and BOT_PUBLIC_URL)


@router.callback_query(F.data == "pay_crypto")
async def cb_crypto(call: CallbackQuery) -> None:
    await call.answer()
    if not _crypto_enabled():
        await call.message.edit_text(
            "🌐 <b>Crypto payments</b> aren't enabled yet.\n"
            "<i>Admin: set HELEKET_API_KEY, HELEKET_MERCHANT_ID and BOT_PUBLIC_URL.</i>",
            reply_markup=kb([btn("🔙 Back", "acc_buy", style="danger")]))
        return
    # pick the coin/network first
    rows = [[btn(label, f"hk_coin:{i}", style="primary")]
            for i, (_c, _n, label) in enumerate(CRYPTO_CHOICES)]
    rows.append([btn("🔙 Back", "acc_buy", style="danger")])
    await call.message.edit_text(
        "🌐 <b>Crypto Payment</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"${BGM_PRICE_USD:g}/BGM · gateway minimum ${MIN_USD_AMOUNT:g}.\n\n"
        "Choose the coin/network you'll pay with:",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("hk_coin:"))
async def cb_coin(call: CallbackQuery) -> None:
    await call.answer()
    idx = int(call.data.split(":", 1)[1])
    if idx >= len(CRYPTO_CHOICES):
        return
    _c, _n, label = CRYPTO_CHOICES[idx]
    rows = [[btn(f"💰 ${u} → {round(u / BGM_PRICE_USD):,} BGM", f"hk_buy:{idx}:{u}",
                 style="success")] for u in _USD_PACKS]
    rows.append([btn("🔙 Back", "pay_crypto", style="danger")])
    await call.message.edit_text(
        f"🌐 <b>{label}</b>\n\nPick an amount — you'll get a secure Heleket pay "
        "page. BGM is credited automatically once the network confirms.",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("hk_buy:"))
async def cb_hk_buy(call: CallbackQuery) -> None:
    _, idx_s, usd_s = call.data.split(":")
    idx, usd = int(idx_s), float(usd_s)
    if idx >= len(CRYPTO_CHOICES) or usd < MIN_USD_AMOUNT:
        await call.answer("Invalid selection.", show_alert=True)
        return
    crypto, network, label = CRYPTO_CHOICES[idx]
    bgm = round(usd / BGM_PRICE_USD)
    await call.answer("Generating invoice…")
    uid = call.from_user.id
    order_id = make_order_id(uid)
    webhook_url = f"{BOT_PUBLIC_URL}/heleket-webhook"
    result = await create_invoice(order_id, usd, crypto, network, webhook_url)
    if not result or not (result.get("url") or result.get("address")):
        await call.message.edit_text(
            "❌ Couldn't reach the payment gateway. Try again shortly.",
            reply_markup=kb([btn("🔙 Back", "pay_crypto", style="danger")]))
        return
    db = await MongoManager.get()
    await db.safe_insert("crypto_orders", {
        "order_id": order_id, "user_id": uid, "bgm": bgm, "amount_usd": usd,
        "crypto": crypto, "network": network,
        "heleket_uuid": result.get("uuid"), "status": "waiting", "created_at": _now(),
    })
    pay_url = result.get("url")
    addr = result.get("address")
    text = (f"🌐 <b>Pay ${usd:.2f}</b> in <b>{label}</b> → <b>{bgm:,} BGM</b>\n"
            "━━━━━━━━━━━━━━━━━━\n")
    rows = []
    if pay_url:
        rows.append([url_btn("💳 Open Pay Page", pay_url, style="success")])
        text += "Tap to pay (valid ~30 min). "
    if addr:
        text += f"\n📥 <b>Address:</b>\n<code>{addr}</code>\n"
    text += "\nBGM lands automatically after confirmation."
    rows.append([btn("🔙 Back", "acc_buy", style="danger")])
    await call.message.edit_text(text, reply_markup=kb(*rows))


# ── Heleket webhook (registered in bot.py at /heleket-webhook) ──────────────────
async def heleket_webhook(request: web.Request) -> web.Response:
    raw = await request.read()
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.Response(status=400, text="bad json")
    if not verify_webhook(raw, str(data.get("sign", ""))):
        logger.warning("Heleket webhook failed signature check")
        return web.Response(status=403, text="bad signature")

    order_id = str(data.get("order_id") or "")
    status = str(data.get("status") or data.get("payment_status") or "")
    if not order_id or status not in PAID_STATUSES:
        return web.Response(text="ignored")

    db = await MongoManager.get()
    # Atomic flip: only the FIRST callback that flips waiting/pending → paid
    # credits BGM. Concurrent duplicate callbacks match nothing and skip.
    order = await db.find_one_and_update_global(
        "crypto_orders",
        {"order_id": order_id, "status": {"$ne": "paid"}},
        {"$set": {"status": "paid", "paid_at": _now()}})
    if not order:
        return web.Response(text="ok")  # unknown / already credited (idempotent)
    await add_bgm(order["user_id"], float(order["bgm"]))
    bot = request.app["bot"]
    try:
        await bot.send_message(order["user_id"],
                               f"🎉 <b>Crypto payment confirmed!</b>\n"
                               f"💎 +{order['bgm']:,} BGM added to your wallet.")
    except Exception:  # noqa: BLE001
        pass
    return web.Response(text="ok")
