"""
utils/email_monitor.py — FamPay UPI auto-verification (ported from inflowads).

Polls the inbox (IMAP, e.g. rajsom8877@gmail.com) for FamPay credit emails,
parses amount + 12-digit UTR (+ FMPIB id), and matches them against pending UPI
payments by UTR AND exact amount (±₹2). On a match it credits BGM via
handlers.payments._confirm_payment.

"Ledger sync": an email can arrive before OR after the user submits their UTR.
Emails with no matching payment yet are parked in `fampay_ledger` (unclaimed);
when the user later submits that UTR, the submit handler claims the ledger row
immediately. So order doesn't matter.

Mongo-only (no Redis). Background task started from bot.py.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    EMAIL_LOG_CHANNEL, FAMPAY_SENDER, IMAP_HOST, IMAP_PASSWORD, IMAP_USER,
)
from database.connection import MongoManager

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60
_LOOK_BACK_MIN = 60
_AMOUNT_TOLERANCE_INR = 2.0
_IMAP_TIMEOUT_SEC = 30
_FETCH_TIMEOUT_SEC = 120

_AMOUNT_RE = re.compile(r'(?:₹|\bINR\b|\bRs\b\.?)\s*([\d,]+(?:\.\d{1,2})?)', re.IGNORECASE)
_UTR_RE = re.compile(r'(?<!\d)(\d{12})(?!\d)')
_FAMPAY_ID_RE = re.compile(r'(FMPIB\d+)', re.IGNORECASE)

_CREDIT_KEYWORDS = [
    'you received', 'received in your famx', 'credited to', 'added to',
    'received from', 'money received', 'amount received', 'has been credited',
    'credited', 'money added', 'has been added', 'received ₹', 'received rs',
]
_DEBIT_KEYWORDS = ['payment of', 'is successful', 'paid to', 'debited', 'sent to', 'you paid']
_UTR_CONTEXT_KEYWORDS = ['utr', 'reference', 'ref no', 'ref.', 'txn', 'transaction id', 'rrn']
_UTR_CONTEXT_MAX_DIST = 60
_AMOUNT_AFTER_CREDIT_MAX_DIST = 80


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_html(html: str) -> str:
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</?(p|div|tr|td|table|h[1-6])[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'[ \t]+', ' ', html).strip()


def _extract_payment_info(subject: str, body: str) -> Optional[dict]:
    combined = f"{subject}\n{body}"
    lc = combined.lower()
    if any(k in lc for k in _DEBIT_KEYWORDS) and not any(k in lc for k in _CREDIT_KEYWORDS):
        return None
    if not any(k in lc for k in _CREDIT_KEYWORDS):
        return None

    amts = list(_AMOUNT_RE.finditer(combined))
    if not amts:
        return None
    amt_m = amts[0]
    ctx = [m.start() for kw in _CREDIT_KEYWORDS for m in re.finditer(re.escape(kw), lc)]
    if ctx:
        after = [m for m in amts
                 if any(0 <= (m.start() - p) <= _AMOUNT_AFTER_CREDIT_MAX_DIST for p in ctx)]
        if after:
            amt_m = min(after, key=lambda m: min(
                (m.start() - p) for p in ctx
                if 0 <= (m.start() - p) <= _AMOUNT_AFTER_CREDIT_MAX_DIST))
        else:
            amt_m = min(amts, key=lambda m: min(abs(m.start() - p) for p in ctx))
    try:
        amount_inr = float(amt_m.group(1).replace(",", ""))
    except ValueError:
        return None
    if amount_inr <= 0:
        return None

    utr = None
    utr_matches = list(_UTR_RE.finditer(combined))
    if utr_matches:
        utr_m = utr_matches[0]
        uctx = [m.start() for kw in _UTR_CONTEXT_KEYWORDS for m in re.finditer(re.escape(kw), lc)]
        if uctx:
            best = min(utr_matches, key=lambda m: min(abs(m.start() - p) for p in uctx))
            if min(abs(best.start() - p) for p in uctx) <= _UTR_CONTEXT_MAX_DIST:
                utr_m = best
        utr = utr_m.group(1)

    fmp = _FAMPAY_ID_RE.search(combined)
    return {"amount_inr": amount_inr, "utr": utr,
            "fmp_id": fmp.group(1).upper() if fmp else None,
            "subject": subject, "body": body}


def _imap_fetch_blocking(host: str, user: str, password: str) -> list[dict]:
    results: list[dict] = []
    since = (datetime.now(timezone.utc) - timedelta(minutes=_LOOK_BACK_MIN)).date()
    try:
        from imap_tools import AND, MailBox
        with MailBox(host, timeout=_IMAP_TIMEOUT_SEC).login(user, password, "INBOX") as mb:
            for msg in mb.fetch(AND(from_=FAMPAY_SENDER, date_gte=since),
                                mark_seen=False, reverse=True):
                if FAMPAY_SENDER.lower() not in (msg.from_ or "").lower():
                    continue
                body = msg.text or ""
                if not body and msg.html:
                    body = _strip_html(msg.html)
                info = _extract_payment_info(msg.subject or "", body)
                if info:
                    info["uid"] = str(msg.uid)
                    info["date"] = msg.date.isoformat() if msg.date else ""
                    results.append(info)
    except Exception as exc:  # noqa: BLE001
        logger.error("IMAP fetch error: %s", exc)
    return results


async def _process_email(info: dict, bot) -> None:
    db = await MongoManager.get()
    amount = info["amount_inr"]
    utr, fmp = info.get("utr"), info.get("fmp_id")
    email_uid = info["uid"]
    matched_id = utr or fmp

    # 1) dedup
    if await db.find_one_global("processed_emails", {"uid": email_uid}):
        return

    # 2) forensic vault + 3) channel log
    try:
        await db.safe_insert("raw_emails", {"uid": email_uid, "subject": info["subject"],
                                            "body": info["body"], "amount_inr": amount,
                                            "utr": utr, "fmp_id": fmp, "logged_at": _now_iso()})
    except Exception:  # noqa: BLE001
        pass
    await _log_email(bot, info)

    # 4) park in ledger as unclaimed (so a UTR submitted later can claim it)
    if matched_id:
        try:
            await db.safe_insert("fampay_ledger", {"uid": email_uid, "utr": matched_id,
                                                   "amount": amount, "status": "unclaimed",
                                                   "created_at": _now_iso()})
        except Exception:  # noqa: BLE001
            pass

    if not matched_id:
        await _mark_processed(db, email_uid, amount, utr, fmp, None)
        return

    # 5) match a pending payment by UTR
    conditions = []
    if utr:
        conditions.append({"submitted_utr": utr})
    if fmp:
        conditions.append({"submitted_utr": fmp})
    doc = await db.find_one_global("payments", {
        "method": "upi", "status": {"$in": ["utr_submitted", "waiting"]}, "$or": conditions})
    if not doc:
        await _mark_processed(db, email_uid, amount, utr, fmp, None)
        return

    # 6) exact-amount verification
    expected = float(doc.get("total_due_inr") or 0)
    if expected <= 0 or abs(expected - amount) > _AMOUNT_TOLERANCE_INR:
        await db.safe_update("payments", {"order_id": doc["order_id"]},
                             {"$set": {"status": "amount_mismatch", "email_amount_inr": amount,
                                       "email_txn_id": matched_id, "failed_at": _now_iso()}})
        await db.safe_update("fampay_ledger", {"uid": email_uid}, {"$set": {"status": "mismatch"}})
        await _mark_processed(db, email_uid, amount, utr, fmp, doc["order_id"])
        try:
            await bot.send_message(
                doc["user_id"],
                f"⚠️ <b>Payment amount mismatch</b>\nWe found UTR <code>{matched_id}</code> but "
                f"the amount (₹{amount:.2f}) doesn't match the order (₹{expected:.2f}).\n"
                "Please contact /support.")
        except Exception:  # noqa: BLE001
            pass
        return

    # 7) confirm + credit (lazy import avoids circular dependency)
    from handlers.payments import _confirm_payment
    await _confirm_payment(doc, bot, email_txn_id=matched_id, email_amount_inr=amount)
    await db.safe_update("fampay_ledger", {"uid": email_uid}, {"$set": {"status": "claimed"}})
    await _mark_processed(db, email_uid, amount, utr, fmp, doc["order_id"])


async def _log_email(bot, info: dict) -> None:
    if not EMAIL_LOG_CHANNEL:
        return
    try:
        await bot.send_message(
            EMAIL_LOG_CHANNEL,
            f"📩 <b>UPI credit detected</b>\n💰 ₹{info['amount_inr']:.2f}\n"
            f"🔖 UTR: <code>{info.get('utr') or 'N/A'}</code> · "
            f"FMPIB: <code>{info.get('fmp_id') or 'N/A'}</code>")
    except Exception:  # noqa: BLE001
        pass


async def _mark_processed(db, uid, amount, utr, fmp, order_id) -> None:
    try:
        await db.safe_insert("processed_emails", {"uid": uid, "amount_inr": amount,
                                                  "txn_id": utr, "fmp_id": fmp,
                                                  "order_id": order_id, "processed_at": _now_iso()})
    except Exception:  # noqa: BLE001
        pass


async def run_email_monitor(bot) -> None:
    if not all([IMAP_HOST, IMAP_USER, IMAP_PASSWORD]):
        logger.warning("UPI email monitor DISABLED — set IMAP_HOST / IMAP_USER / IMAP_PASSWORD.")
        return
    logger.info("UPI email monitor started (every %ds).", POLL_INTERVAL)
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                emails = await asyncio.wait_for(
                    loop.run_in_executor(None, _imap_fetch_blocking,
                                         IMAP_HOST, IMAP_USER, IMAP_PASSWORD),
                    timeout=_FETCH_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                logger.error("IMAP fetch exceeded %ds — skipping poll.", _FETCH_TIMEOUT_SEC)
                continue
            for info in emails:
                try:
                    await _process_email(info, bot)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Email processing error: %s", exc, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Email monitor stopped.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Email monitor loop error: %s", exc, exc_info=True)
