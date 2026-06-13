"""
utils/oxapay.py — Oxapay crypto payments (merchant API).

create_invoice() asks Oxapay for a hosted pay page for a USD amount; the user
pays any supported coin. Oxapay then POSTs a signed callback to our webhook,
which we authenticate with verify_hmac() before crediting BGM.

Reads OXAPAY_MERCHANT from config. Field names follow Oxapay's merchants API;
if their schema shifts, adjust here only.
"""
import hashlib
import hmac
import logging

import aiohttp

from config import OXAPAY_MERCHANT

logger = logging.getLogger(__name__)

_CREATE_URL = "https://api.oxapay.com/merchants/request"


async def create_invoice(amount_usd: float, order_id: str, callback_url: str,
                         return_url: str = "") -> dict | None:
    """Return {'pay_link', 'track_id'} or None on failure."""
    if not OXAPAY_MERCHANT:
        return None
    body = {
        "merchant": OXAPAY_MERCHANT,
        "amount": round(amount_usd, 2),
        "currency": "USD",
        "lifeTime": 30,            # minutes the pay page stays valid
        "feePaidByPayer": 1,
        "orderId": order_id,
        "callbackUrl": callback_url,
    }
    if return_url:
        body["returnUrl"] = return_url
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(_CREATE_URL, json=body) as r:
                data = await r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Oxapay create failed: %s", exc)
        return None
    if str(data.get("result")) != "100":
        logger.warning("Oxapay error: %s", data)
        return None
    return {"pay_link": data.get("payLink"), "track_id": data.get("trackId")}


def verify_hmac(raw_body: bytes, hmac_header: str) -> bool:
    """Authenticate a callback: HMAC-SHA512(raw_body, merchant_key) == header."""
    if not OXAPAY_MERCHANT or not hmac_header:
        return False
    expected = hmac.new(OXAPAY_MERCHANT.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, hmac_header)


def is_paid(status: str) -> bool:
    return str(status).lower() in ("paid", "confirmed", "complete", "completed")
