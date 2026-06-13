"""
utils/heleket.py — Heleket crypto payment gateway (mirrors the inflowads setup).

Sign:    md5( base64(json_body) + api_key )
Invoice: POST /v1/payment  → result {uuid, address, url, ...}
Status:  POST /v1/payment/info {uuid}
Webhook: Heleket POSTs JSON with a "sign" member INSIDE the body; we strip that
         member from the RAW bytes and recompute the signature (fails closed if
         the API key is unset). Paid statuses: "paid", "paid_over".

Config: HELEKET_API_KEY, HELEKET_MERCHANT_ID.
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

from config import HELEKET_API_KEY, HELEKET_MERCHANT_ID

logger = logging.getLogger(__name__)

BASE_URL = "https://api.heleket.com"
MIN_USD_AMOUNT = 5.0          # Heleket gateway minimum
PAYMENT_LIFETIME = 1800       # seconds the invoice stays payable

# Coins offered, each with its primary network (label shown to the user).
CRYPTO_CHOICES = [
    ("USDT", "tron",     "USDT · TRC-20"),
    ("USDT", "bsc",      "USDT · BEP-20"),
    ("BTC",  "bitcoin",  "Bitcoin"),
    ("ETH",  "ethereum", "Ethereum"),
    ("BNB",  "bsc",      "BNB · BEP-20"),
    ("LTC",  "litecoin", "Litecoin"),
    ("TRX",  "tron",     "TRON"),
    ("SOL",  "solana",   "Solana"),
    ("TON",  "ton",      "Toncoin"),
    ("DOGE", "dogecoin", "Dogecoin"),
]

PAID_STATUSES = {"paid", "paid_over"}


# ── signing ──────────────────────────────────────────────────────────────────
def _sign(data: dict) -> str:
    body = json.dumps(data, sort_keys=True, separators=(",", ":"))
    encoded = base64.b64encode(body.encode()).decode()
    return hashlib.md5((encoded + HELEKET_API_KEY).encode()).hexdigest()


def _headers(data: dict) -> dict:
    return {"merchant": HELEKET_MERCHANT_ID, "sign": _sign(data),
            "Content-Type": "application/json"}


_SIGN_LEADING = re.compile(r',\s*"sign"\s*:\s*"[0-9a-fA-F]+"')
_SIGN_TRAILING = re.compile(r'"sign"\s*:\s*"[0-9a-fA-F]+"\s*,\s*')
_SIGN_BARE = re.compile(r'"sign"\s*:\s*"[0-9a-fA-F]+"')


def verify_webhook(raw_body: bytes, incoming_sign: str) -> bool:
    """True iff `incoming_sign` matches the body. Fails closed without a key."""
    if not HELEKET_API_KEY:
        logger.error("Heleket webhook: API key unset — rejecting (fail closed).")
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
        expected = hashlib.md5((encoded + HELEKET_API_KEY).encode()).hexdigest()
        if _hmac.compare_digest(expected, incoming_sign):
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Heleket raw verify failed, trying re-serialize: %s", exc)
    # fallback: re-serialize parsed JSON the PHP way (no sort, escape slashes)
    try:
        data = json.loads(raw_body.decode("utf-8"))
        data.pop("sign", None)
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("/", "\\/")
        encoded = base64.b64encode(body.encode()).decode()
        expected = hashlib.md5((encoded + HELEKET_API_KEY).encode()).hexdigest()
        return _hmac.compare_digest(expected, incoming_sign)
    except Exception as exc:  # noqa: BLE001
        logger.error("Heleket webhook verify error: %s", exc)
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
                    logger.warning("Heleket %s: non-JSON HTTP %d", path, r.status)
                    return None
                return await r.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Heleket %s failed: %s", path, exc)
        return None


async def create_invoice(order_id: str, usd_amount: float, crypto: str,
                         network: str, webhook_url: str) -> Optional[dict]:
    payload = {
        "amount": f"{usd_amount:.2f}", "currency": "USD",
        "to_currency": crypto, "network": network, "order_id": order_id,
        "url_callback": webhook_url, "lifetime": PAYMENT_LIFETIME,
        "is_payment_multiple": False,
    }
    data = await _post("/v1/payment", payload, timeout=15)
    if not data or data.get("state") != 0:
        logger.error("Heleket create_invoice failed: %s", data)
        return None
    return data.get("result")


async def fetch_status(heleket_uuid: str) -> Optional[dict]:
    if not heleket_uuid:
        return None
    data = await _post("/v1/payment/info", {"uuid": heleket_uuid}, timeout=10)
    if not data or data.get("state") != 0:
        return None
    return data.get("result")
