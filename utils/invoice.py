"""
utils/invoice.py — professional payment invoices.

One polished, branded receipt for EVERY payment made inside the bot — UPI top-up,
crypto top-up, Premium (money or BGM), and the per-file overage. A single builder
so every invoice looks identical and professional, with the exact amount, currency,
payment mode, date/time, the payer's name, an invoice/reference number and a PAID
stamp. The same invoice is mirrored to the admin log channel for the operator's
records.

House rule: amounts are rendered through utils.format.fmt_amount (never raw / `:g`).
"""
import logging
from datetime import datetime, timezone
from html import escape

from config import ADMIN_LOG_CHANNEL_ID
from utils.brand import BOT_NAME, DIVIDER, THIN_RULE
from utils.format import fmt_amount

logger = logging.getLogger(__name__)

# Currency symbol by code. Anything else (e.g. "BGM") is rendered as a suffix.
_SYM = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def money(amount, currency: str) -> str:
    """Human, professional money string: '₹250', '$3', '1,000 BGM'."""
    cur = (currency or "").upper()
    if cur in ("BGM", "BCN") or not cur:
        unit = f" {cur}" if cur else ""
        return f"{fmt_amount(amount)}{unit}"
    sym = _SYM.get(cur)
    return f"{sym}{fmt_amount(amount)}" if sym else f"{fmt_amount(amount)} {cur}"


async def _billed_to(bot, uid: int) -> str:
    name, uname = "Reader", ""
    try:
        chat = await bot.get_chat(uid)
        full = " ".join(x for x in [getattr(chat, "first_name", "") or "",
                                    getattr(chat, "last_name", "") or ""] if x).strip()
        name = full or name
        uname = getattr(chat, "username", "") or ""
    except Exception:  # noqa: BLE001 — never let a name lookup break the receipt
        pass
    line = escape(name)
    if uname:
        line += f" (@{escape(uname)})"
    return line


def _invoice_no(prefix: str, uid: int, when: datetime) -> str:
    return f"{prefix}-{uid}-{when.strftime('%Y%m%d-%H%M%S')}"


def _build(*, inv_no: str, when: datetime, billed: str, uid: int, item: str,
           method: str, reference: str, amount, currency: str, status: str,
           note: str) -> str:
    lines = [
        f"🧾 <b>INVOICE</b> · {escape(BOT_NAME)}",
        DIVIDER,
        f"<b>Invoice no.</b>  <code>{escape(inv_no)}</code>",
        f"<b>Date</b>  {when.strftime('%d %b %Y, %H:%M UTC')}",
        f"<b>Billed to</b>  {billed}",
        f"<b>Account ID</b>  <code>{uid}</code>",
        THIN_RULE,
        f"<b>Description</b>  {escape(item)}",
        f"<b>Payment mode</b>  {escape(method)}",
    ]
    if reference:
        lines.append(f"<b>Reference</b>  <code>{escape(str(reference))}</code>")
    lines += [
        THIN_RULE,
        f"<b>Amount paid</b>  <b>{money(amount, currency)}</b>",
        f"<b>Status</b>  ✅ <b>{escape(status)}</b>",
        DIVIDER,
        "<i>Thank you for your purchase. Please keep this invoice for your records.</i>",
    ]
    if note:
        lines.append(f"<i>{escape(note)}</i>")
    return "\n".join(lines)


async def send_invoice(bot, uid: int, *, item: str, amount, currency: str,
                       method: str, reference: str = "", order_id: str = "",
                       when: datetime | None = None, prefix: str = "INV",
                       status: str = "PAID", note: str = "") -> None:
    """Send a professional invoice to the user (and mirror it to the admin log).

    item      — what was bought ("Wallet Top-Up", "Premium · 30 days", a file name…)
    amount    — numeric amount paid
    currency  — "INR" | "USD" | "BGM" | …
    method    — payment mode shown verbatim ("UPI · FamPay", "Crypto · OxaPay", …)
    reference — UTR / txid / track id (optional)
    order_id  — used as the invoice number when present, else one is generated
    """
    when = when or _now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    inv_no = str(order_id) if order_id else _invoice_no(prefix, uid, when)
    billed = await _billed_to(bot, uid)
    body = _build(inv_no=inv_no, when=when, billed=billed, uid=uid, item=item,
                  method=method, reference=reference, amount=amount,
                  currency=currency, status=status, note=note)
    try:
        await bot.send_message(uid, body)
    except Exception as exc:  # noqa: BLE001 — a receipt must never break the payment
        logger.warning("invoice send to user %s failed: %s", uid, exc)
    # operator's records — best effort, never raises
    if ADMIN_LOG_CHANNEL_ID:
        try:
            await bot.send_message(ADMIN_LOG_CHANNEL_ID, "🧾 <b>Invoice issued</b>\n" + body)
        except Exception:  # noqa: BLE001
            pass
