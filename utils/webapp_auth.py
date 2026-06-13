"""
utils/webapp_auth.py — Telegram Mini App initData validation.

Every Mini App request carries Telegram's signed `initData`. We verify the
HMAC so a user can't forge their identity or replay another user's session.
This is the trust anchor for all server-side scoring / token credits.

Algorithm (per Telegram docs):
  secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
  expected   = HMAC_SHA256(key=secret_key, msg=data_check_string)
  data_check_string = "\n".join(sorted "k=v" pairs, excluding hash)
"""
import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

from config import BOT_TOKEN

_MAX_AGE = 24 * 3600  # reject initData older than 24h


def verify_init_data(init_data: str) -> Optional[dict]:
    """Return the parsed user dict if the signature is valid, else None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:  # noqa: BLE001
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    # freshness — blocks replay of an old captured initData
    try:
        if time.time() - int(pairs.get("auth_date", 0)) > _MAX_AGE:
            return None
    except (ValueError, TypeError):
        return None

    try:
        return json.loads(pairs.get("user", "null"))
    except Exception:  # noqa: BLE001
        return None


def user_id_from(init_data: str) -> Optional[int]:
    user = verify_init_data(init_data)
    return int(user["id"]) if user and "id" in user else None
