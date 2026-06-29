"""
utils/oxapay.py — OxaPay crypto payment gateway.

OxaPay (https://oxapay.com) is a no-KYC, instant-API-key crypto gateway that's
popular in the Telegram-bot ecosystem (sign up with email/Google/Telegram,
generate one Merchant API key, go live — no approval wait). We create a
USD-priced invoice; the hosted pay page lets the payer settle in any coin the
merchant has enabled (USDT TRC-20/ERC-20/BEP-20, BTC, ETH, USDC, …).

Invoice: POST /v1/payment/invoice   header: merchant_api_key
         body {amount, currency:"USD", lifetime, callback_url, order_id, …}
         → {track_id, payment_url, expired_at, …}
Webhook: OxaPay POSTs JSON to callback_url with an `HMAC` header =
         HMAC_SHA512(raw_body, MERCHANT_API_KEY). Statuses arrive as "Paying"
         then "Paid"; we credit on "Paid". The endpoint must return HTTP 200
         with body "ok".

Config: OXAPAY_MERCHANT_API_KEY.
"""
import hashlib
import hmac as _hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from config import OXAPAY_MERCHANT_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://api.oxapay.com/v1"
MIN_USD_AMOUNT = 1.0          # practical floor; OxaPay also enforces per-coin minimums
INVOICE_LIFETIME_MIN = 60     # minutes the invoice stays payable (15–2880)

# Shown to the user so they know the pay page accepts the full OxaPay coin list.
POPULAR_COINS = "USDT (TRC-20/ERC-20/BEP-20) · BTC · ETH · USDC · TRX · BNB · LTC · DOGE + more"

# OxaPay sends "Paying" (seen on chain) then "Paid" (confirmed & credited).
PAID_STATUSES = {"paid"}


def make_order_id(user_id: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"BB-{user_id}-{ts}-{uuid.uuid4().hex[:8].upper()}"


# ── webhook signature ────────────────────────────────────────────────────────
def verify_webhook(raw_body: bytes, hmac_header: str) -> bool:
    """True iff the `HMAC` header matches HMAC-SHA512(raw_body, api_key).
    Fails closed when the key is unset (so a missing key can never auto-credit)."""
    if not OXAPAY_MERCHANT_API_KEY:
        logger.error("OxaPay webhook: API key unset — rejecting (fail closed).")
        return False
    if not isinstance(hmac_header, str) or not hmac_header:
        return False
    try:
        expected = _hmac.new(OXAPAY_MERCHANT_API_KEY.encode(), raw_body,
                             hashlib.sha512).hexdigest()
        return _hmac.compare_digest(expected, hmac_header.strip())
    except Exception as exc:  # noqa: BLE001
        logger.error("OxaPay webhook verify error: %s", exc)
        return False


# ── API ──────────────────────────────────────────────────────────────────────
async def create_invoice(order_id: str, usd_amount: float,
                         callback_url: str) -> Optional[dict]:
    """Create a USD-priced OxaPay invoice. Returns {url, track_id} where `url`
    is the hosted pay page (payer picks any enabled coin), or None on failure."""
    if not OXAPAY_MERCHANT_API_KEY:
        return None
    payload = {
        "amount": round(float(usd_amount), 2),
        "currency": "USD",
        "lifetime": INVOICE_LIFETIME_MIN,
        "callback_url": callback_url,
        "order_id": order_id,
        "description": f"Wallet top-up ({order_id})",
        # fee_paid_by_payer=1 → the payer covers OxaPay's processing fee on top of
        # `amount`. OxaPay's API doesn't expose the fee figure, so the portal can't
        # show a live number; instead it discloses that OxaPay (an independent
        # platform) may add a small fee, and the exact total is shown on OxaPay's
        # checkout page before the payer confirms.
        "fee_paid_by_payer": 1,
    }
    headers = {"merchant_api_key": OXAPAY_MERCHANT_API_KEY,
               "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(f"{BASE_URL}/payment/invoice",
                              data=json.dumps(payload), headers=headers) as r:
                body = await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OxaPay create_invoice failed: %s", exc)
        return None
    # v1 may wrap the result in a `data` object; accept either shape.
    result = (body.get("data") if isinstance(body, dict)
              and isinstance(body.get("data"), dict) else body)
    if not isinstance(result, dict) or not result.get("payment_url"):
        logger.error("OxaPay create_invoice unexpected response: %s", body)
        return None
    return {"url": result.get("payment_url"), "track_id": result.get("track_id")}
