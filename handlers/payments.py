"""
handlers/payments.py — Buy BGM (UPI email-auto-verified + crypto via OxaPay).

UPI flow (FamPay receipt auto-verify, no admin step):
  💎 Buy BGM → 💳 UPI → enter BGM amount → pay the shown UPI ID/QR →
  submit UTR (validated + de-duplicated) → the IMAP email monitor reads the
  FamPay credit email, matches UTR + exact amount (±₹2) → auto-credits BGM.
  The fampay_ledger absorbs emails that arrive before/after the UTR; a single
  atomic flip in _confirm_payment guarantees exactly one credit.

Crypto flow (OxaPay gateway):
  🌐 Crypto → pick a USD pack (≥ gateway min) → OxaPay invoice (USD-priced, coin
  NOT locked, so the hosted pay page offers every coin you've enabled) →
  HMAC-verified /oxapay-webhook credits BGM automatically once the network
  confirms. Activates when OXAPAY_MERCHANT_API_KEY + BOT_PUBLIC_URL are set.
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
    ADMIN_IDS, BGM_PRICE_INR, BGM_PRICE_USD, BOT_PUBLIC_URL, IMAP_PASSWORD,
    IMAP_USER, MIN_BGM_PURCHASE, OXAPAY_MERCHANT_API_KEY, PAYMENT_QR_URL, UPI_ID,
)
from database.connection import MongoManager
from utils.bundles import bonus_for, tiers_blurb
from utils.oxapay import (
    MIN_USD_AMOUNT, PAID_STATUSES, POPULAR_COINS, create_invoice, make_order_id,
    verify_webhook,
)
from utils.format import fmt_amount
from utils.keyboards import btn, kb, url_btn, webapp_btn
from utils.settings import get_float
from utils.wallet import add_bgm


async def _first_purchase_bonus(uid: int, base: float) -> float:
    """Grant a one-time first-purchase bonus (% of the base BGM), exactly once
    ever per user. The atomic flag flip guarantees only the first paid order
    triggers it, even across concurrent confirmations."""
    pct = await get_float("first_purchase_pct")
    if pct <= 0:
        return 0.0
    db = await MongoManager.get()
    flipped = await db.find_one_and_update_global(
        "users", {"user_id": uid, "first_purchase_done": {"$ne": True}},
        {"$set": {"first_purchase_done": True}})
    return round(base * pct / 100.0, 2) if flipped else 0.0

logger = logging.getLogger(__name__)
router = Router()


class PayFSM(StatesGroup):
    awaiting_amount = State()   # UPI: how many BGM
    awaiting_utr = State()      # UPI: the transaction reference
    awaiting_coupon = State()   # apply a promo coupon


async def _pop_active_coupon(db, uid: int) -> str:
    """Read & clear the user's applied coupon (one application per order)."""
    u = await db.find_one_global("users", {"user_id": uid}, {"active_coupon": 1}) or {}
    code = u.get("active_coupon") or ""
    if code:
        await db.safe_update("users", {"user_id": uid}, {"$set": {"active_coupon": ""}})
    return code


def _now():
    return datetime.now(timezone.utc)


# Accept a standard 12-digit bank UTR or a FamPay FMPIB id.
_UTR_OK = re.compile(r'^(?:\d{12}|FMPIB\d+)$', re.IGNORECASE)
_AMOUNT_TOLERANCE_INR = 2.0
# How long an order's pay-portal session is shown as valid (cosmetic countdown).
# The order itself stays matchable by the email monitor even after this elapses,
# so a slightly-late UPI payment still credits.
_UPI_TTL_SEC = 1200      # 20 min
_CRYPTO_TTL_SEC = 3600   # 60 min (matches the OxaPay invoice lifetime)


def _upi_enabled() -> bool:
    return bool(IMAP_USER and IMAP_PASSWORD)


# ── Buy menu ─────────────────────────────────────────────────────────────────
@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    text, markup = await _buy_view()
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "acc_buy")
async def cb_buy(call: CallbackQuery) -> None:
    await call.answer()
    # stamp the "cart" so the abandoned-cart nudge can follow up if they don't pay
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": call.from_user.id},
                         {"$set": {"cart_opened_at": _now(), "cart_nudged": False}})
    text, markup = await _buy_view()
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


async def _buy_view():
    from utils.deals import banner
    deal = await banner()
    inr_price = await get_float("bgm_price_inr")
    usd_price = await get_float("bgm_price_usd")
    min_inr = int(MIN_BGM_PURCHASE * inr_price)
    fp_pct = await get_float("first_purchase_pct")
    fp_line = f"🥳 <b>First purchase:</b> +{fmt_amount(fp_pct)}% bonus BGM!\n" if fp_pct > 0 else ""
    text = (
        "<b>💎 Buy BookGems (BGM)</b>\n"
        + (f"{deal}\n" if deal else "")
        + "━━━━━━━━━━━━━━━━━━\n"
        "Permanent tokens — never expire.\n\n"
        f"🏦 <b>UPI (INR):</b> ₹{fmt_amount(inr_price)}/BGM · min {MIN_BGM_PURCHASE} (₹{min_inr})\n"
        f"🌐 <b>Crypto:</b> ${fmt_amount(usd_price)}/BGM\n"
        f"🎁 <b>Bonus BGM:</b> {tiers_blurb()}\n"
        + fp_line +
        "\n<i>Both auto-credit — UPI is verified from the payment receipt, crypto "
        "on-chain.</i>"
    )
    return text, kb(
        [btn("💳 Pay via UPI (INR)", "pay_upi", style="success")],
        [btn("🌐 Pay via Crypto", "pay_crypto", style="primary")],
        [btn("🎟️ Apply Coupon", "pay_coupon", style="primary")],
        [btn("🔙 Back", "menu_account", style="danger")],
    )


@router.callback_query(F.data == "pay_coupon")
async def cb_coupon(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(PayFSM.awaiting_coupon)
    await call.message.edit_text("🎟️ <b>Apply a Coupon</b>\n\nSend your coupon code "
                                 "to add a bonus to your next purchase. /cancel to abort.")


@router.message(PayFSM.awaiting_coupon, F.text)
async def on_coupon(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    from utils.coupons import validate
    ok, res = await validate(raw, message.chat.id)
    if not ok:
        msg = {"unknown": "That coupon isn't valid.", "expired": "That coupon has expired.",
               "exhausted": "That coupon is fully claimed.", "used": "You've already used that coupon."}
        await message.answer(f"❌ {msg.get(res, 'Coupon not accepted.')}")
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": message.chat.id},
                         {"$set": {"active_coupon": raw.strip().upper()}})
    kind = res.get("kind")
    val = res.get("value")
    desc = f"+{fmt_amount(val)}% bonus" if kind == "pct" else f"+{fmt_amount(val)} BGM bonus"
    await message.answer(f"✅ <b>Coupon applied!</b> ({desc})\nIt'll be added to your next "
                         "purchase. Open 💎 Buy BGM to pay.",
                         reply_markup=kb([btn("💎 Buy BGM", "acc_buy", style="success")]))


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
    min_inr = int(MIN_BGM_PURCHASE * await get_float("bgm_price_inr"))
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
    inr = round(bgm * await get_float("bgm_price_inr"), 2)
    from utils.deals import deal_bonus
    bonus = bonus_for(bgm) + await deal_bonus(bgm)
    uid = message.chat.id
    order_id = make_order_id(uid)
    db = await MongoManager.get()
    await db.safe_insert("payments", {
        "order_id": order_id, "user_id": uid, "username": message.from_user.username or "",
        "method": "upi", "bgm": bgm, "bonus": bonus, "total_due_inr": inr,
        "coupon": await _pop_active_coupon(db, uid),
        "status": "waiting", "submitted_utr": None, "created_at": _now(),
        "expires_at": (_now() + timedelta(seconds=_UPI_TTL_SEC)).isoformat(),
    })
    bonus_line = f"🎁 <b>Bonus:</b> +{fmt_amount(bonus)} BGM!\n" if bonus else ""

    # Preferred: the dedicated Secure Payment Portal (Mini App) — UPI QR + in-app
    # UTR submission + live status. Falls back to the in-chat UTR flow when no
    # public HTTPS URL is configured (Telegram only opens web_app over HTTPS).
    if BOT_PUBLIC_URL:
        await state.clear()
        await message.answer(
            f"<b>💳 Pay ₹{inr:.2f}</b> for <b>{bgm} BGM</b>\n{bonus_line}"
            "━━━━━━━━━━━━━━━━━━\n"
            "Tap below to open the <b>Secure Payment Portal</b> — scan the UPI QR, "
            "pay, then submit your UTR right inside it. BGM is credited automatically.",
            reply_markup=kb(
                [webapp_btn("💳 Open Secure Payment Portal", "pay.html",
                            query=f"order_id={order_id}", style="success")],
                [btn("🔙 Back", "acc_buy", style="danger")]))
        return

    # Fallback: collect the UTR in chat.
    await state.update_data(order_id=order_id, total=inr)
    await state.set_state(PayFSM.awaiting_utr)
    rows = []
    if PAYMENT_QR_URL:
        rows.append([url_btn("📷 View QR", PAYMENT_QR_URL)])
    await message.answer(
        f"<b>💳 Pay ₹{inr:.2f}</b> for <b>{bgm} BGM</b>\n{bonus_line}"
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
    from utils.coupons import redeem as _redeem_coupon
    base = float(flipped.get("bgm") or 0)
    bonus = float(flipped.get("bonus") or 0)
    fp = await _first_purchase_bonus(flipped["user_id"], base)
    cpn = await _redeem_coupon(flipped.get("coupon", ""), flipped["user_id"], base)
    total = base + bonus + fp + cpn
    await add_bgm(flipped["user_id"], total)
    await db.safe_update("users", {"user_id": flipped["user_id"]},
                         {"$unset": {"cart_opened_at": ""}}, upsert=False)  # cart completed
    bonus_line = f" (incl. +{fmt_amount(bonus)} bonus)" if bonus else ""
    fp_line = f"\n🥳 First-purchase bonus: <b>+{fmt_amount(fp)} BGM</b>!" if fp else ""
    cpn_line = f"\n🎟️ Coupon bonus: <b>+{fmt_amount(cpn)} BGM</b>!" if cpn else ""
    try:
        await bot.send_message(
            flipped["user_id"],
            f"🎉 <b>Payment confirmed!</b>\n💎 <b>+{fmt_amount(total)} BGM</b>{bonus_line} added.{fp_line}{cpn_line}\n"
            f"🧾 Ref: <code>{email_txn_id or flipped.get('submitted_utr','')}</code>")
    except Exception:  # noqa: BLE001
        pass


# ── crypto (OxaPay) ──────────────────────────────────────────────────────────
# Packs are USD-denominated (BGM shown too). The invoice is USD-priced and does
# NOT lock a coin, so the OxaPay pay page offers every coin you've enabled.
_USD_PACKS = [5, 10, 25, 50, 100]


def _crypto_enabled() -> bool:
    return bool(OXAPAY_MERCHANT_API_KEY and BOT_PUBLIC_URL)


@router.callback_query(F.data == "pay_crypto")
async def cb_crypto(call: CallbackQuery) -> None:
    await call.answer()
    if not _crypto_enabled():
        await call.message.edit_text(
            "🌐 <b>Crypto payments</b> aren't enabled yet.\n"
            "<i>Admin: set OXAPAY_MERCHANT_API_KEY and BOT_PUBLIC_URL.</i>",
            reply_markup=kb([btn("🔙 Back", "acc_buy", style="danger")]))
        return
    usd_price = await get_float("bgm_price_usd")
    rows = []
    for u in _USD_PACKS:
        b = round(u / usd_price)
        bn = bonus_for(b)
        extra = f" +{fmt_amount(bn)}🎁" if bn else ""
        rows.append([btn(f"💰 ${u} → {b:,} BGM{extra}", f"cm_buy:{u}", style="success")])
    rows.append([btn("🔙 Back", "acc_buy", style="danger")])
    await call.message.edit_text(
        "🌐 <b>Crypto Payment (OxaPay)</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"${fmt_amount(usd_price)}/BGM · gateway minimum ${fmt_amount(MIN_USD_AMOUNT)}.\n"
        f"💱 <b>Pay with:</b> {POPULAR_COINS}\n\n"
        "Pick an amount — you'll get a secure OxaPay pay page where you choose "
        "your coin. BGM is credited automatically once the network confirms.",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("cm_buy:"))
async def cb_cm_buy(call: CallbackQuery) -> None:
    try:
        usd = float(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("Invalid selection.", show_alert=True)
        return
    if usd < MIN_USD_AMOUNT:
        await call.answer("Amount below the gateway minimum.", show_alert=True)
        return
    bgm = round(usd / await get_float("bgm_price_usd"))
    from utils.deals import deal_bonus
    bonus = bonus_for(bgm) + await deal_bonus(bgm)
    await call.answer("Generating invoice…")
    uid = call.from_user.id
    order_id = make_order_id(uid)
    webhook_url = f"{BOT_PUBLIC_URL}/oxapay-webhook"
    result = await create_invoice(order_id, usd, webhook_url)
    if not result or not result.get("url"):
        await call.message.edit_text(
            "❌ Couldn't reach the payment gateway. Try again shortly.",
            reply_markup=kb([btn("🔙 Back", "pay_crypto", style="danger")]))
        return
    db = await MongoManager.get()
    pay_url = result.get("url")
    await db.safe_insert("crypto_orders", {
        "order_id": order_id, "user_id": uid, "bgm": bgm, "bonus": bonus,
        "amount_usd": usd, "gateway": "oxapay", "method": "crypto",
        "coupon": await _pop_active_coupon(db, uid),
        "track_id": result.get("track_id"), "pay_url": pay_url or "",
        "status": "waiting", "created_at": _now(),
        "expires_at": (_now() + timedelta(seconds=_CRYPTO_TTL_SEC)).isoformat(),
    })

    # Preferred: the unified Secure Payment Portal (Mini App) — opens the OxaPay
    # checkout and shows live BGM-credit status in-app.
    if BOT_PUBLIC_URL:
        await call.message.edit_text(
            f"🌐 <b>Pay ${usd:.2f}</b> in crypto → <b>{bgm:,} BGM</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Open the <b>Secure Payment Portal</b> to pick your coin and pay. "
            "BGM lands automatically once the network confirms.",
            reply_markup=kb(
                [webapp_btn("💳 Open Secure Payment Portal", "pay.html",
                            query=f"order_id={order_id}", style="success")],
                [btn("🔙 Back", "acc_buy", style="danger")]))
        return

    # Fallback: direct OxaPay hosted pay-page link.
    rows = [[url_btn("💳 Open Pay Page", pay_url, style="success")],
            [btn("🔙 Back", "acc_buy", style="danger")]]
    await call.message.edit_text(
        f"🌐 <b>Pay ${usd:.2f}</b> in crypto → <b>{bgm:,} BGM</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Tap to open the pay page (valid ~60 min) and pick your coin.\n"
        "BGM lands automatically after confirmation.",
        reply_markup=kb(*rows))


# ── OxaPay webhook (registered in bot.py at /oxapay-webhook) ────────────────────
async def oxapay_webhook(request: web.Request) -> web.Response:
    raw = await request.read()
    # Verify against the raw bytes BEFORE parsing (HMAC-SHA512, key = API key).
    if not verify_webhook(raw, request.headers.get("HMAC", "")):
        logger.warning("OxaPay webhook failed signature check")
        return web.Response(status=403, text="bad signature")
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.Response(status=400, text="bad json")

    order_id = str(data.get("order_id") or "")
    status = str(data.get("status") or "").lower()
    # OxaPay sends "Paying" first, then "Paid". Acknowledge non-paid callbacks
    # with 200/"ok" so OxaPay stops retrying; only "Paid" credits below.
    if not order_id or status not in PAID_STATUSES:
        return web.Response(text="ok")

    db = await MongoManager.get()
    # Atomic flip: only the FIRST callback that flips waiting/pending → paid
    # credits BGM. Concurrent duplicate callbacks match nothing and skip.
    order = await db.find_one_and_update_global(
        "crypto_orders",
        {"order_id": order_id, "status": {"$ne": "paid"}},
        {"$set": {"status": "paid", "paid_at": _now()}})
    if not order:
        return web.Response(text="ok")  # unknown / already credited (idempotent)
    from utils.coupons import redeem as _redeem_coupon
    base = float(order.get("bgm") or 0)
    fp = await _first_purchase_bonus(order["user_id"], base)
    cpn = await _redeem_coupon(order.get("coupon", ""), order["user_id"], base)
    total = base + float(order.get("bonus") or 0) + fp + cpn
    await add_bgm(order["user_id"], total)
    await db.safe_update("users", {"user_id": order["user_id"]},
                         {"$unset": {"cart_opened_at": ""}}, upsert=False)  # cart completed
    fp_line = f"\n🥳 First-purchase bonus: +{fmt_amount(fp)} BGM!" if fp else ""
    cpn_line = f"\n🎟️ Coupon bonus: +{fmt_amount(cpn)} BGM!" if cpn else ""
    bot = request.app["bot"]
    try:
        await bot.send_message(order["user_id"],
                               f"🎉 <b>Crypto payment confirmed!</b>\n"
                               f"💎 +{fmt_amount(total)} BGM added to your wallet.{fp_line}{cpn_line}")
    except Exception:  # noqa: BLE001
        pass
    return web.Response(text="ok")
