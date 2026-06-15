"""
utils/settings.py — live-editable runtime settings (no redeploy).

Money levers admins can tune in-bot, stored in Mongo `kv` under "set:<key>".
Handlers read these at runtime so changes take effect immediately. Defaults
fall back to config constants.
"""
from typing import Any

from config import BGM_PRICE_INR, BGM_PRICE_USD
from database.connection import MongoManager

# key → (default, label, kind, category) — kind drives the editor's validation,
# category groups the levers in the admin panel.
DEFAULTS: dict[str, tuple] = {
    # Pricing — what things cost
    "download_cost":   (1.0, "Download cost (tokens)", "float", "Pricing"),
    "request_cost":    (2.0, "Manual request cost (tokens)", "float", "Pricing"),
    "ai_cost":         (1.0, "AI feature cost (tokens)", "float", "Pricing"),
    "bgm_price_inr":   (BGM_PRICE_INR, "BGM price (₹)", "float", "Pricing"),
    "bgm_price_usd":   (BGM_PRICE_USD, "BGM price ($)", "float", "Pricing"),
    # Rewards — what users earn
    "claim_min":       (3.0, "Daily claim min (BCN)", "float", "Rewards"),
    "claim_max":       (5.0, "Daily claim max (BCN)", "float", "Rewards"),
    "referrer_bonus":  (0.5, "Referrer bonus (BGM)", "float", "Rewards"),
    "referee_bonus":   (0.25, "New-user referral bonus (BGM)", "float", "Rewards"),
    # Economy — conversion rules
    "convert_tax_pct": (25.0, "BCN→BGM convert tax (%)", "float", "Economy"),
    "convert_min_bgm": (50.0, "Convert: min BGM required", "float", "Economy"),
    # Safety — anti-abuse flood limiter (set flood_max very high to disable)
    "flood_max":        (20.0, "Flood: max actions / window", "float", "Safety"),
    "flood_window_sec": (10.0, "Flood: window (seconds)", "float", "Safety"),
}


async def get_setting(key: str, default: Any = None) -> Any:
    db = await MongoManager.get()
    val = await db.kv_get(f"set:{key}", None)
    if val is not None:
        return val
    if default is not None:
        return default
    return DEFAULTS.get(key, (None,))[0]


async def get_float(key: str) -> float:
    return float(await get_setting(key, DEFAULTS[key][0]))


async def set_setting(key: str, value: Any) -> None:
    db = await MongoManager.get()
    await db.kv_set(f"set:{key}", value)


async def all_settings() -> dict[str, Any]:
    return {k: await get_setting(k, d[0]) for k, d in DEFAULTS.items()}
