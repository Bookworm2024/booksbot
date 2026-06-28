"""
utils/logs.py — structured activity logging to the two Telegram channels.

ADMIN_LOG_CHANNEL_ID (private, staff-only)
    Every meaningful user activity, in full detail — user id, username, amounts,
    payment references. Users are NEVER given a link to this channel.

PUBLIC_LOG_CHANNEL_ID (community feed)
    Curated, privacy-safe highlights only — first names at most, never user ids,
    usernames or payment references. This is the channel users receive a
    one-time join link to (handlers/invite.py).

Every helper is best-effort: a logging failure must never break a user flow, so
all sends are wrapped and swallow errors. Call them with `await` but you can
fire-and-forget — they never raise.
"""
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from config import ADMIN_LOG_CHANNEL_ID, PUBLIC_LOG_CHANNEL_ID
from utils.format import fmt_amount

logger = logging.getLogger(__name__)

DIV = "━━━━━━━━━━━━━━━━━━━━"
_IST = timezone(timedelta(hours=5, minutes=30))   # operator timezone (India)


def stamp() -> str:
    """Human, on-brand timestamp, e.g. '28 Jun 2026 · 08:47 PM IST'."""
    return datetime.now(_IST).strftime("%d %b %Y · %I:%M %p IST")


def _u(s) -> str:
    """HTML-safe rendering of any dynamic value."""
    return escape(str(s if s is not None else ""))


async def _send(bot, chat_id: int, text: str, photo) -> None:
    if not chat_id:
        return
    try:
        if photo:
            await bot.send_photo(chat_id, photo, caption=text[:1024])
        else:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001 — logging must never break a user flow
        logger.debug("channel log to %s failed", chat_id, exc_info=True)


async def admin_log(bot, text: str, *, photo=None) -> None:
    """Detailed, private staff log."""
    await _send(bot, ADMIN_LOG_CHANNEL_ID, text, photo)


async def public_log(bot, text: str, *, photo=None) -> None:
    """Curated, privacy-safe community feed."""
    await _send(bot, PUBLIC_LOG_CHANNEL_ID, text, photo)


# ── event helpers (each fans out to admin [detailed] + public [privacy-safe]) ──
async def log_new_user(bot, uid: int, first_name: str, username: str = "") -> None:
    at = stamp()
    handle = f" (@{_u(username)})" if username else ""
    await admin_log(
        bot,
        f"🆕 <b>New User</b>\n{DIV}\n"
        "<blockquote>"
        f"👤 <b>{_u(first_name) or 'Reader'}</b>{handle}\n"
        f"🆔 <code>{uid}</code>\n"
        f"🕒 {at}</blockquote>")
    await public_log(
        bot,
        "🎉 <b>A new reader just joined!</b>\n" + DIV + "\n"
        f"<blockquote>👋 Welcome aboard, <b>{_u(first_name) or 'Reader'}</b>!\n"
        f"🕒 {at}</blockquote>\n"
        "📚 Another book lover in the <b>Books Provider</b> family.")


async def log_book_found(bot, uid: int, name: str, ext: str = "",
                         currency: str = "") -> None:
    """A user searched the archive and a book was delivered to them."""
    at = stamp()
    fmt = f" · <code>.{_u(ext).upper()}</code>" if ext else ""
    await admin_log(
        bot,
        f"📦 <b>Book Delivered</b>\n{DIV}\n"
        "<blockquote>"
        f"📖 <b>{_u(name) or 'Untitled'}</b>{fmt}\n"
        f"👤 <code>{uid}</code>\n"
        f"💳 Paid in: <b>{_u(currency) or '—'}</b>\n"
        f"🕒 {at}</blockquote>")
    await public_log(
        bot,
        "📖 <b>Just delivered from the library</b>\n" + DIV + "\n"
        f"<blockquote>✨ A reader unlocked <b>{_u(name) or 'a new title'}</b>{fmt}\n"
        f"🕒 {at}</blockquote>\n"
        "🔎 Find yours — just send a title to the bot.")


async def log_request_created(bot, uid: int, first_name: str, title: str,
                              author: str = "", category: str = "",
                              cover_id: str | None = None) -> None:
    at = stamp()
    cat = _u(category).title() or "Book"
    await admin_log(
        bot,
        f"🚀 <b>New {cat} Request</b>\n{DIV}\n"
        "<blockquote>"
        f"📖 <b>{_u(title)}</b>\n"
        f"✍️ {_u(author) or '—'}\n"
        f"👤 <b>{_u(first_name)}</b> (<code>{uid}</code>)\n"
        f"🕒 {at}</blockquote>",
        photo=cover_id)
    await public_log(
        bot,
        f"📝 <b>New {cat} request</b>\n" + DIV + "\n"
        f"<blockquote>📖 <b>{_u(title)}</b>"
        + (f"\n✍️ {_u(author)}" if author else "") + "\n"
        f"🕒 {at}</blockquote>\n"
        "🙌 Our team is on the hunt — fulfilled requests land here too.",
        photo=cover_id)


async def log_request_fulfilled(bot, uid: int, title: str, author: str = "",
                                rid: str = "", cover_id: str | None = None) -> None:
    at = stamp()
    await admin_log(
        bot,
        f"✅ <b>Request Fulfilled</b>\n{DIV}\n"
        "<blockquote>"
        f"🆔 <code>{_u(rid)}</code>\n"
        f"📖 <b>{_u(title)}</b>\n"
        f"✍️ {_u(author) or '—'}\n"
        f"👤 <code>{uid}</code>\n"
        f"🕒 {at}</blockquote>",
        photo=cover_id)
    await public_log(
        bot,
        "✅ <b>Request fulfilled!</b>\n" + DIV + "\n"
        f"<blockquote>📖 <b>{_u(title)}</b>"
        + (f"\n✍️ {_u(author)}" if author else "") + "\n"
        f"🕒 {at}</blockquote>\n"
        "🎁 Another reader got exactly what they asked for.",
        photo=cover_id)


async def log_purchase(bot, uid: int, bgm_total: float, paid_label: str = "",
                       method: str = "") -> None:
    """A confirmed BGM purchase. `paid_label` is a ready string like '₹40' / '$5'."""
    at = stamp()
    meth = {"upi": "UPI", "crypto": "Crypto"}.get((method or "").lower(), _u(method) or "—")
    paid = f" ({_u(paid_label)})" if paid_label else ""
    await admin_log(
        bot,
        f"💰 <b>Payment Received</b>\n{DIV}\n"
        "<blockquote>"
        f"💎 <b>+{fmt_amount(bgm_total)} BGM</b>\n"
        f"💳 Amount: <b>{_u(paid_label) or '—'}</b> · {meth}\n"
        f"👤 <code>{uid}</code>\n"
        f"🕒 {at}</blockquote>")
    await public_log(
        bot,
        "💎 <b>Someone just powered up!</b>\n" + DIV + "\n"
        f"<blockquote>🚀 A reader unlocked <b>{fmt_amount(bgm_total)} BGM</b>{paid}\n"
        f"🕒 {at}</blockquote>\n"
        "💛 Thank you for keeping the library free for everyone.")


async def log_bcn_claim(bot, uid: int, amount: float) -> None:
    at = stamp()
    await admin_log(
        bot,
        f"🎁 <b>Daily Claim</b>\n{DIV}\n"
        "<blockquote>"
        f"🪙 <b>+{fmt_amount(amount)} BCN</b>\n"
        f"👤 <code>{uid}</code>\n"
        f"🕒 {at}</blockquote>")
    await public_log(
        bot,
        "🎁 <b>Daily reward claimed</b>\n" + DIV + "\n"
        f"<blockquote>🪙 A reader grabbed <b>{fmt_amount(amount)} free BCN</b>\n"
        f"🕒 {at}</blockquote>\n"
        "⚡ Your free coins are waiting too — tap /claim every day.")
