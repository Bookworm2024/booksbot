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
import random
import string
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from aiohttp import web

from config import (
    ADMIN_IDS, BGM_PRICE_INR, BGM_PRICE_USD, BOT_PUBLIC_URL, HELEKET_API_KEY,
    HELEKET_MERCHANT_ID, MIN_BGM_PURCHASE, PAYMENT_QR_URL, UPI_ID,
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
    awaiting_utr = State()
    awaiting_screenshot = State()
    admin_amount = State()


def _pid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def _now():
    return datetime.now(timezone.utc)


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
        f"🌐 <b>Crypto:</b> ${BGM_PRICE_USD:g}/BGM · min {MIN_BGM_PURCHASE}\n\n"
        "<i>UPI is verified manually by admins; crypto is automatic.</i>"
    )
    return text, kb(
        [btn("💳 Pay via UPI (INR)", "pay_upi", style="success")],
        [btn("🌐 Pay via Crypto", "pay_crypto", style="primary")],
        [btn("🔙 Back", "menu_account", style="danger")],
    )


# ── UPI ──────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "pay_upi")
async def cb_upi(call: CallbackQuery) -> None:
    await call.answer()
    min_inr = int(MIN_BGM_PURCHASE * BGM_PRICE_INR)
    text = (
        "<b>💳 UPI Payment</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>UPI ID:</b> <code>{UPI_ID}</code>\n"
        f"💰 <b>Min:</b> {MIN_BGM_PURCHASE} BGM = ₹{min_inr}\n\n"
        "1️⃣ Pay the amount to the UPI ID (or scan the QR).\n"
        "2️⃣ Tap <b>Paid</b> and send your 12-digit UTR + screenshot.\n\n"
        "<i>Verified manually — allow a little time.</i>"
    )
    rows = []
    if PAYMENT_QR_URL:
        rows.append([url_btn("📷 View QR", PAYMENT_QR_URL)])
    rows.append([btn("✅ Paid", "pay_paid", style="success")])
    rows.append([btn("🔙 Back", "acc_buy", style="danger")])
    await call.message.edit_text(text, reply_markup=kb(*rows))


@router.callback_query(F.data == "pay_paid")
async def cb_paid(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(PayFSM.awaiting_utr)
    await call.message.answer("🧾 Send your <b>UTR / transaction reference</b> "
                              "(the number on your payment receipt) — /cancel to abort:")


@router.message(PayFSM.awaiting_utr, F.text)
async def on_utr(message: Message, state: FSMContext) -> None:
    utr = (message.text or "").strip()
    if utr.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    if not utr.isdigit() or len(utr) < 9:
        await message.answer("⚠️ That doesn't look like a valid UTR. Re-check your receipt.")
        return
    db = await MongoManager.get()
    if await db.find_one_global("payments", {"utr": utr}):
        await state.clear()
        await message.answer("❌ This UTR has already been submitted.")
        return
    await state.update_data(utr=utr)
    await state.set_state(PayFSM.awaiting_screenshot)
    await message.answer("📸 Now send the <b>payment screenshot</b> (photo or file):")


@router.message(PayFSM.awaiting_screenshot, F.photo | F.document)
async def on_screenshot(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    utr = data.get("utr")
    shot = message.photo[-1].file_id if message.photo else message.document.file_id
    uid = message.chat.id
    pid = _pid()
    db = await MongoManager.get()
    await db.safe_insert("payments", {
        "payment_id": pid, "user_id": uid, "method": "upi", "utr": utr,
        "screenshot": shot, "status": "pending", "created_at": _now(),
    })
    await message.answer("✅ <b>Submitted!</b> Your payment is in the verification queue. "
                         "You'll be notified once approved.")
    caption = (f"📥 <b>UPI Deposit</b>\n🆔 <code>{pid}</code>\n"
               f"👤 <a href='tg://user?id={uid}'>{message.from_user.first_name}</a> "
               f"(<code>{uid}</code>)\n🧾 UTR: <code>{utr}</code>")
    akb = kb([btn("✅ Approve", f"pay_ok:{pid}", style="success"),
              btn("❌ Decline", f"pay_no:{pid}", style="danger")])
    for admin in ADMIN_IDS:
        try:
            await message.bot.send_photo(admin, shot, caption=caption, reply_markup=akb)
        except Exception:  # noqa: BLE001
            pass


# ── admin approve / decline ─────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("pay_ok:"))
async def cb_approve(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    pid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    pay = await db.find_one_global("payments", {"payment_id": pid})
    if not pay or pay.get("status") != "pending":
        await call.answer("Already processed.", show_alert=True)
        return
    await call.answer()
    await state.set_state(PayFSM.admin_amount)
    await state.update_data(pid=pid, target=pay["user_id"])
    await call.message.answer(f"💎 How many <b>BGM</b> to credit for <code>{pid}</code>?")


@router.message(PayFSM.admin_amount, F.text)
async def on_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.replace(".", "", 1).isdigit():
        await message.answer("❌ Enter a number.")
        return
    amount = float(raw)
    pid, target = data.get("pid"), data.get("target")
    db = await MongoManager.get()
    pay = await db.find_one_global("payments", {"payment_id": pid})
    if not pay or pay.get("status") != "pending":
        await message.answer("❌ Already processed.")
        return
    await add_bgm(target, amount)
    await db.safe_update("payments", {"payment_id": pid},
                         {"$set": {"status": "approved", "amount_bgm": amount,
                                   "approved_by": message.chat.id, "approved_at": _now()}})
    await message.answer(f"✅ Credited {amount:g} BGM to <code>{target}</code>.")
    try:
        await message.bot.send_message(
            target, f"🎉 <b>Payment approved!</b>\n💎 <b>+{amount:g} BGM</b> added to your wallet.")
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("pay_no:"))
async def cb_decline(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    pid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    pay = await db.find_one_global("payments", {"payment_id": pid})
    if not pay or pay.get("status") != "pending":
        await call.answer("Already processed.", show_alert=True)
        return
    await db.safe_update("payments", {"payment_id": pid}, {"$set": {"status": "declined"}})
    await call.answer("Declined")
    try:
        await call.bot.send_message(
            pay["user_id"], "❌ <b>Payment declined.</b> If you believe this is a mistake, "
            "contact support via /support with your receipt.")
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
