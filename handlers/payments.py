"""
handlers/payments.py — Buy BGM (UPI manual; crypto stub until Oxapay key).

UPI flow (no external dependency):
  💎 Buy BGM → 💳 UPI → shows UPI ID + QR + pricing → ✅ Paid →
  enter UTR (validated + de-duplicated) → upload screenshot →
  admins get the proof with ✅ Approve / ❌ Decline →
  Approve → admin enters BGM amount → credited + user notified.

Crypto (Oxapay) is wired as a placeholder; it activates once OXAPAY_MERCHANT
is set (the actual address-generation call goes in here then).
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

from config import (
    ADMIN_IDS, BGM_PRICE_INR, BGM_PRICE_USD, MIN_BGM_PURCHASE, OXAPAY_MERCHANT,
    PAYMENT_QR_URL, UPI_ID,
)
from database.connection import MongoManager
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
    await call.message.answer("🧾 Send your <b>12-digit UTR / transaction ID</b> "
                              "(/cancel to abort):")


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


# ── crypto (gated on Oxapay key) ────────────────────────────────────────────────
@router.callback_query(F.data == "pay_crypto")
async def cb_crypto(call: CallbackQuery) -> None:
    await call.answer()
    if not OXAPAY_MERCHANT:
        await call.message.edit_text(
            "🌐 <b>Crypto payments</b> aren't enabled yet.\n"
            "<i>Admin: set OXAPAY_MERCHANT to activate automatic crypto deposits.</i>",
            reply_markup=kb([btn("🔙 Back", "acc_buy", style="danger")]))
        return
    # Oxapay static-address generation goes here once the key is configured.
    await call.message.edit_text(
        "🌐 <b>Crypto</b> — coming online. (Oxapay integration pending final wiring.)",
        reply_markup=kb([btn("🔙 Back", "acc_buy", style="danger")]))
