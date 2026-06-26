"""
utils/cryptomus.py — Cryptomus crypto payment gateway.

Cryptomus (https://cryptomus.com) accepts almost every major coin/network. We
create a single USD-denominated invoice WITHOUT locking a coin, so the hosted
pay page lets the payer choose ANY currency Cryptomus supports (BTC, ETH, USDT
on TRC-20/ERC-20/BEP-20, BNB, SOL, TON, LTC, TRX, DOGE, USDC, …). That maximises
coin coverage and removes the risk of an invalid `network` code rejecting an
invoice.

Sign:    md5( base64(json_body) + api_key )           (Cryptomus request-format spec)
Invoice: POST /v1/payment        → result {uuid, address, url, ...}
Status:  POST /v1/payment/info   {uuid}
Webhook: Cryptomus POSTs JSON with a "sign" member INSIDE the body; we strip that
         member from the RAW bytes and recompute the signature (fails closed if
         the API key is unset). Paid statuses: "paid", "paid_over".

Config: CRYPTOMUS_API_KEY, CRYPTOMUS_MERCHANT_ID.
"""
import base64
import hashlib
import hmac as _hmac
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from config import CRYPTOMUS_API_KEY, CRYPTOMUS_MERCHANT_ID

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cryptomus.com"
MIN_USD_AMOUNT = 1.0          # Cryptomus general per-invoice minimum (~$1)
PAYMENT_LIFETIME = 3600       # seconds the invoice stays payable (300–43200)

# Shown to the user so they know the page accepts the full Cryptomus coin list.
POPULAR_COINS = "BTC · ETH · USDT (TRC-20/ERC-20/BEP-20) · BNB · SOL · TON · LTC · TRX · DOGE · USDC + more"

PAID_STATUSES = {"paid", "paid_over"}


# ── signing ──────────────────────────────────────────────────────────────────
def _sign(data: dict) -> str:
    body = json.dumps(data, sort_keys=True, separators=(",", ":"))
    encoded = base64.b64encode(body.encode()).decode()
    return hashlib.md5((encoded + CRYPTOMUS_API_KEY).encode()).hexdigest()


def _headers(data: dict) -> dict:
    return {"merchant": CRYPTOMUS_MERCHANT_ID, "sign": _sign(data),
            "Content-Type": "application/json"}


_SIGN_LEADING = re.compile(r',\s*"sign"\s*:\s*"[0-9a-fA-F]+"')
_SIGN_TRAILING = re.compile(r'"sign"\s*:\s*"[0-9a-fA-F]+"\s*,\s*')
_SIGN_BARE = re.compile(r'"sign"\s*:\s*"[0-9a-fA-F]+"')


def verify_webhook(raw_body: bytes, incoming_sign: str) -> bool:
    """True iff `incoming_sign` matches the body. Fails closed without a key."""
    if not CRYPTOMUS_API_KEY:
        logger.error("Cryptomus webhook: API key unset — rejecting (fail closed).")
        return False
    if not isinstance(incoming_sign, str) or len(incoming_sign) != 32:
        return False
    try:
        raw = raw_body.decode("utf-8")
        stripped, n = _SIGN_LEADING.subn("", raw, count=1)
        if n == 0:
            stripped, n = _SIGN_TRAILING.subn("", raw, count=1)
        if n == 0:
            stripped, n = _SIGN_BARE.subn("", raw, count=1)
        encoded = base64.b64encode(stripped.encode()).decode()
        expected = hashlib.md5((encoded + CRYPTOMUS_API_KEY).encode()).hexdigest()
        if _hmac.compare_digest(expected, incoming_sign):
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Cryptomus raw verify failed, trying re-serialize: %s", exc)
    # fallback: re-serialize parsed JSON the PHP way (no sort, escape slashes)
    try:
        data = json.loads(raw_body.decode("utf-8"))
        data.pop("sign", None)
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("/", "\\/")
        encoded = base64.b64encode(body.encode()).decode()
        expected = hashlib.md5((encoded + CRYPTOMUS_API_KEY).encode()).hexdigest()
        return _hmac.compare_digest(expected, incoming_sign)
    except Exception as exc:  # noqa: BLE001
        logger.error("Cryptomus webhook verify error: %s", exc)
        return False


def make_order_id(user_id: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"BB-{user_id}-{ts}-{uuid.uuid4().hex[:8].upper()}"


# ── API ──────────────────────────────────────────────────────────────────────
async def _post(path: str, payload: dict, timeout: float) -> Optional[dict]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.post(f"{BASE_URL}{path}", data=body, headers=_headers(payload)) as r:
                if "application/json" not in (r.headers.get("Content-Type") or "").lower():
                    logger.warning("Cryptomus %s: non-JSON HTTP %d", path, r.status)
                    return None
                return await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cryptomus %s failed: %s", path, exc)
        return None


async def create_invoice(order_id: str, usd_amount: float, webhook_url: str,
                         *, to_currency: Optional[str] = None,
                         network: Optional[str] = None) -> Optional[dict]:
    """Create a USD-priced Cryptomus invoice. Leaving `to_currency`/`network`
    unset (the default) yields a pay page offering every coin Cryptomus
    supports; pass them only to lock a specific coin/network."""
    payload = {
        "amount": f"{usd_amount:.2f}", "currency": "USD",
        "order_id": order_id, "url_callback": webhook_url,
        "lifetime": PAYMENT_LIFETIME,
    }
    if to_currency:
        payload["to_currency"] = to_currency
    if network:
        payload["network"] = network
    data = await _post("/v1/payment", payload, timeout=15)
    if not data or data.get("state") != 0:
        logger.error("Cryptomus create_invoice failed: %s", data)
        return None
    return data.get("result")


async def fetch_status(cryptomus_uuid: str) -> Optional[dict]:
    if not cryptomus_uuid:
        return None
    data = await _post("/v1/payment/info", {"uuid": cryptomus_uuid}, timeout=10)
    if not data or data.get("state") != 0:
        return None
    return data.get("result")
