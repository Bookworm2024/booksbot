"""
handlers/pay_api.py — backend for the payment Mini App (web_app/pay.html).

The "Secure Payment Portal" mirrors the inflowads unified Mini-App flow, adapted
to BooksBot's BGM economy. All endpoints are initData-authenticated (the same
HMAC trust anchor as the reader/games Mini Apps) and ownership-checked: a caller
can only see / act on their OWN order.

Endpoints (registered in bot.py):
  GET  /api/pay/status?order_id=…   bootstrap + poll an order's live state
  POST /api/pay/ipaid  {order_id, utr}   submit a UTR / FamPay id for a UPI order
  POST /api/pay/cancel {order_id}        abort a still-waiting order

UPI crediting is identical to the chat flow and the email monitor: the submitted
UTR is matched against the SHARED FamPay inbox (same Gmail both bots poll). The
fampay_ledger pre-match credits instantly when the credit email already arrived;
otherwise the email monitor credits when it lands. `_confirm_payment`'s atomic
status flip guarantees exactly one credit — so whichever surface (this Mini App,
the chat fallback, or the monitor) matches first is the one that credits.
"""
import logging

from aiohttp import web

from config import UPI_ID
from database.connection import MongoManager
from utils.webapp_auth import user_id_from

logger = logging.getLogger(__name__)

MERCHANT_NAME = "BooksBot"


def _init_data(request: web.Request, body: dict | None = None) -> str:
    """initData can ride the X-Telegram-Init-Data header (Mini-App convention),
    a query param, or the JSON body — accept any so the client stays simple."""
    return (
        request.headers.get("X-Telegram-Init-Data")
        or request.query.get("init_data")
        or (body or {}).get("init_data")
        or ""
    )


async def _uid(request: web.Request, body: dict | None = None) -> int | None:
    return user_id_from(_init_data(request, body))


async def _find_order(db, order_id: str) -> tuple[dict | None, str]:
    """Locate an order by id. Returns (doc, kind) where kind is 'upi' (payments)
    or 'crypto' (crypto_orders), or (None, '')."""
    if not order_id:
        return None, ""
    doc = await db.find_one_global("payments", {"order_id": order_id})
    if doc:
        return doc, "upi"
    doc = await db.find_one_global("crypto_orders", {"order_id": order_id})
    if doc:
        return doc, "crypto"
    return None, ""


# ── GET /api/pay/status ──────────────────────────────────────────────────────
async def api_pay_status(request: web.Request) -> web.Response:
    uid = await _uid(request)
    if not uid:
        return web.json_response({"ok": False, "error": "auth_failed"}, status=401)
    order_id = request.query.get("order_id", "")
    db = await MongoManager.get()
    doc, kind = await _find_order(db, order_id)
    if not doc:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    if int(doc.get("user_id") or 0) != uid:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    out = {
        "ok": True,
        "method": "upi" if kind == "upi" else "crypto",
        "status": doc.get("status", "waiting"),
        "order_id": order_id,
        "bgm": float(doc.get("bgm") or 0),
        "bonus": float(doc.get("bonus") or 0),
        "expires_at": doc.get("expires_at"),
        "merchant": MERCHANT_NAME,
    }
    if kind == "upi":
        out.update({
            "upi_id": UPI_ID,
            "total_due_inr": float(doc.get("total_due_inr") or 0),
            "submitted_utr": doc.get("submitted_utr"),
        })
    else:
        out.update({
            "amount_usd": float(doc.get("amount_usd") or 0),
            "pay_url": doc.get("pay_url") or "",
        })
    return web.json_response(out)


# ── POST /api/pay/ipaid ──────────────────────────────────────────────────────
async def api_pay_ipaid(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    uid = await _uid(request, body)
    if not uid:
        return web.json_response({"success": False, "error": "auth_failed"}, status=401)

    order_id = str(body.get("order_id") or "")
    # Lazy import keeps the (heavy) payments handler off this module's import path
    # and avoids any import-order surprise; payments.py never imports pay_api.
    from handlers.payments import _UTR_OK, _AMOUNT_TOLERANCE_INR, _confirm_payment, _now

    utr = str(body.get("utr") or "").strip().upper().replace(" ", "").replace("-", "")
    if not _UTR_OK.match(utr):
        return web.json_response(
            {"success": False, "error": "Enter a valid 12-digit UTR or FMPIB id."}, status=400)

    db = await MongoManager.get()
    # reject a UTR already consumed by a confirmed payment (cross-order replay)
    if await db.find_one_global("payments", {"submitted_utr": utr, "status": "paid"}):
        return web.json_response(
            {"success": False, "error": "This transaction reference was already used."}, status=409)

    order = await db.find_one_global("payments", {"order_id": order_id})
    if not order or order.get("method") != "upi":
        return web.json_response({"success": False, "error": "not_found"}, status=404)
    if int(order.get("user_id") or 0) != uid:
        return web.json_response({"success": False, "error": "forbidden"}, status=403)
    if order.get("status") not in ("waiting", "utr_submitted"):
        return web.json_response(
            {"success": False, "error": "This order can no longer accept a reference."}, status=409)

    await db.safe_update("payments", {"order_id": order_id},
                         {"$set": {"submitted_utr": utr, "status": "utr_submitted",
                                   "utr_submitted_at": _now()}})

    bot = request.app["bot"]
    # Ledger pre-match: the credit email may already be parked (arrived first).
    total = float(order.get("total_due_inr") or 0)
    led = await db.find_one_global("fampay_ledger", {"utr": utr, "status": "unclaimed"})
    if led and abs(float(led.get("amount") or 0) - total) <= _AMOUNT_TOLERANCE_INR:
        order["submitted_utr"] = utr
        await _confirm_payment(order, bot, email_txn_id=utr,
                               email_amount_inr=float(led.get("amount") or total))
        await db.safe_update("fampay_ledger", {"utr": utr}, {"$set": {"status": "claimed"}})
        return web.json_response({"success": True, "status": "paid"})

    return web.json_response({"success": True, "status": "utr_submitted"})


# ── POST /api/pay/cancel ─────────────────────────────────────────────────────
async def api_pay_cancel(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    uid = await _uid(request, body)
    if not uid:
        return web.json_response({"success": False, "error": "auth_failed"}, status=401)
    order_id = str(body.get("order_id") or "")
    db = await MongoManager.get()
    doc, kind = await _find_order(db, order_id)
    if not doc:
        return web.json_response({"success": False, "error": "not_found"}, status=404)
    if int(doc.get("user_id") or 0) != uid:
        return web.json_response({"success": False, "error": "forbidden"}, status=403)
    # Only a still-waiting order may be aborted — never one that already has a UTR
    # in flight (the payment may be settling) or that is paid.
    if doc.get("status") != "waiting":
        return web.json_response(
            {"success": False, "error": "This order can no longer be cancelled."}, status=409)
    coll = "payments" if kind == "upi" else "crypto_orders"
    from datetime import datetime, timezone
    await db.safe_update(coll, {"order_id": order_id},
                         {"$set": {"status": "cancelled",
                                   "cancelled_at": datetime.now(timezone.utc)}})
    return web.json_response({"success": True})
