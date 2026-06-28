"""
handlers/revenue.py — admin money dashboard.

💰 Revenue (super-admin) → totals collected (UPI ₹ + crypto $), BGM sold,
today's take, paid-order count, and top buyers. Read-only; aggregates the
`payments` (UPI) and `crypto_orders` (OxaPay) collections across clusters.
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from database.connection import MongoManager
from utils.format import fmt_amount
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


def _start_of_day():
    n = datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


async def _paid_docs(coll: str):
    """All paid docs in a collection across clusters."""
    db = await MongoManager.get()
    return await db.find_global(coll, {"status": "paid"})


async def _build() -> str:
    upi = await _paid_docs("payments")
    crypto = await _paid_docs("crypto_orders")
    sod = _start_of_day()

    inr_total = sum(float(d.get("total_due_inr") or d.get("email_amount_inr") or 0) for d in upi)
    usd_total = sum(float(d.get("amount_usd") or 0) for d in crypto)
    bgm_sold = sum(float(d.get("bgm") or 0) for d in upi + crypto)
    orders = len(upi) + len(crypto)

    def _is_today(d):
        ts = d.get("paid_at") or d.get("created_at")
        return isinstance(ts, datetime) and ts >= sod

    inr_today = sum(float(d.get("total_due_inr") or 0) for d in upi if _is_today(d))
    usd_today = sum(float(d.get("amount_usd") or 0) for d in crypto if _is_today(d))
    orders_today = sum(1 for d in upi + crypto if _is_today(d))

    # top buyers by BGM purchased
    buyers: dict[int, float] = {}
    for d in upi + crypto:
        uid = d.get("user_id")
        if uid is not None:
            buyers[uid] = buyers.get(uid, 0) + float(d.get("bgm") or 0)
    top = sorted(buyers.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_lines = "\n".join(f"  {i}. <code>{u}</code> — <code>{fmt_amount(b)}</code> 💎 BGM"
                          for i, (u, b) in enumerate(top, 1)) or "  <i>No buyers yet — the first sale will appear here.</i>"

    # rough gross (INR + crypto converted at a nominal ₹85/$ for a single figure)
    gross_inr = inr_total + usd_total * 85

    return (
        "📊 <b>Revenue Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Every paid order, all currencies, in one ledger.</i>\n\n"
        "<blockquote>"
        f"🧾 <b>Paid orders</b> · <code>{orders}</code>\n"
        f"💎 <b>BGM sold</b> · <code>{fmt_amount(bgm_sold)}</code>"
        "</blockquote>\n"
        "💱 <b>Collected to date</b>\n"
        "<blockquote>"
        f"🏦 UPI (India) · <code>₹{inr_total:,.2f}</code>\n"
        f"🌐 Crypto (OxaPay) · <code>${usd_total:,.2f}</code>\n"
        f"≈ <b>Gross</b> · <code>₹{gross_inr:,.0f}</code>  <i>(crypto valued at ≈ ₹85/$)</i>"
        "</blockquote>\n"
        "⚡ <b>Today so far</b>\n"
        "<blockquote>"
        f"🧾 Orders · <code>{orders_today}</code>\n"
        f"🏦 UPI · <code>₹{inr_today:,.0f}</code>   🌐 Crypto · <code>${usd_today:,.2f}</code>"
        "</blockquote>\n"
        "🏆 <b>Top buyers</b>\n"
        "<blockquote>"
        f"{top_lines}"
        "</blockquote>\n"
        "<i>💡 Read-only snapshot — tap Refresh anytime for live totals.</i>"
    )


@router.message(Command("revenue"))
async def cmd_revenue(message: Message) -> None:
    if message.chat.id != SUPER_ADMIN_ID:
        await message.answer(
            "🔒 <b>Owner only</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>The revenue ledger is reserved for the super admin.</i>")
        return
    await message.answer(await _build())


@router.callback_query(F.data == "admin_revenue")
async def cb_revenue(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _build(),
        reply_markup=kb([btn("🔄 Refresh totals", "admin_revenue", style="primary")],
                        [btn("🔙 Back to Admin", "admin_open", style="danger")]))
