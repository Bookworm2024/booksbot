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
    "bgm_price_inr":   (BGM_PRICE_INR, "BGM price (₹)", "float", "Pricing"),
    "bgm_price_usd":   (BGM_PRICE_USD, "BGM price ($)", "float", "Pricing"),
    # Premium — the freemium tier (real-money + BGM paths)
    "premium_price_inr": (280.0, "Premium price (₹ / 30d)", "float", "Premium"),
    "premium_price_usd": (3.0, "Premium price ($ / 30d)", "float", "Premium"),
    "premium_days":      (30.0, "Premium duration (days, money)", "float", "Premium"),
    "premium_bgm_cost":  (1000.0, "Premium: BGM to redeem 7d", "float", "Premium"),
    "premium_bgm_days":  (7.0, "Premium: days per BGM redeem", "float", "Premium"),
    "overage_price_inr": (100.0, "Per-file overage (₹)", "float", "Premium"),
    "overage_price_usd": (2.0, "Per-file overage ($)", "float", "Premium"),
    # Quotas — free vs premium 24h limits (per user). 0 = closed, -1 = unlimited.
    "q_dl_free":         (2.0, "Free: archive files / 24h", "float", "Quotas"),
    "q_dl_premium":      (-1.0, "Premium: archive files / 24h", "float", "Quotas"),
    "q_mreq_free":       (1.0, "Free: admin ebook requests / 24h", "float", "Quotas"),
    "q_mreq_premium":    (3.0, "Premium: admin ebook requests / 24h", "float", "Quotas"),
    "q_mreq_audio_free":    (0.0, "Free: admin audiobook requests / 24h", "float", "Quotas"),
    "q_mreq_audio_premium": (3.0, "Premium: admin audiobook requests / 24h", "float", "Quotas"),
    "q_airec_free":      (2.0, "Free: AI rec searches / 24h", "float", "Quotas"),
    "q_airec_premium":   (5.0, "Premium: AI rec searches / 24h", "float", "Quotas"),
    "q_aisum_free":      (1.0, "Free: AI summaries / 24h", "float", "Quotas"),
    "q_aisum_premium":   (5.0, "Premium: AI summaries / 24h", "float", "Quotas"),
    "q_game_free":       (2.0, "Free: plays per game / 24h", "float", "Quotas"),
    "q_game_premium":    (5.0, "Premium: plays per game / 24h", "float", "Quotas"),
    # Harvester — automated public-domain book ingestion (utils/harvester.py)
    "harvest_weekly_cap":   (0.0, "Harvester: max files / week (0 = unlimited)", "float", "Harvester"),
    "harvest_interval_sec": (75.0, "Harvester: seconds between files", "float", "Harvester"),
    "harvest_max_mb":       (45.0, "Harvester: max file size (MB)", "float", "Harvester"),
    # Rewards — what users earn (BGM is the earnable reward currency)
    "claim_min":       (3.0, "Daily claim min (BGM)", "float", "Rewards"),
    "claim_max":       (5.0, "Daily claim max (BGM)", "float", "Rewards"),
    "referrer_bonus":  (0.5, "Referrer bonus (BGM)", "float", "Rewards"),
    "referee_bonus":   (0.25, "New-user referral bonus (BGM)", "float", "Rewards"),
    "first_purchase_pct": (20.0, "First-purchase bonus (%)", "float", "Rewards"),
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
    # .get (not DEFAULTS[key]) so a removed/legacy lever resolves to 0.0 instead
    # of KeyError — after the freemium migration a stray "download_cost" read just
    # means "free", which is the intended behaviour.
    default = DEFAULTS.get(key, (0.0,))[0]
    try:
        return float(await get_setting(key, default))
    except (TypeError, ValueError):
        return float(default)


async def set_setting(key: str, value: Any) -> None:
    db = await MongoManager.get()
    await db.kv_set(f"set:{key}", value)


async def all_settings() -> dict[str, Any]:
    return {k: await get_setting(k, d[0]) for k, d in DEFAULTS.items()}
