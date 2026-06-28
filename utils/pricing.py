"""
utils/pricing.py — dynamic download pricing.

Two admin-controlled levers layered on top of the base `download_cost`:

  • Happy Hour — a timed, global discount on downloads ("downloads 50% off for
    6h"). Drives engagement bursts. Stored in kv (hh_factor, hh_until); the
    window is checked live so it auto-expires.
  • Surge — a per-item surcharge for hot titles (the most-downloaded books cost a
    little more). OFF by default so nothing changes until an admin enables it;
    capped at a configurable max so it can never gouge.

The effective download factor is `happy_factor (≤1) × surge_factor (≥1)`, applied
in handlers.request alongside the VIP discount.
"""
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager


def _now():
    return datetime.now(timezone.utc)


# ── Happy Hour ────────────────────────────────────────────────────────────────
async def happy_hour() -> dict:
    """{active, factor, until}. factor is the price multiplier (<1 = discount)."""
    db = await MongoManager.get()
    factor = float(await db.kv_get("hh_factor", 1.0) or 1.0)
    until = await db.kv_get("hh_until", None)
    active = (0.0 < factor < 1.0 and isinstance(until, datetime) and until > _now())
    return {"active": active, "factor": factor if active else 1.0,
            "until": until if active else None}


async def set_happy_hour(pct_off: int, hours: float) -> datetime:
    """Enable a discount of `pct_off`% for `hours`. Returns the end time."""
    pct_off = max(1, min(90, int(pct_off)))
    factor = round(1.0 - pct_off / 100.0, 4)
    until = _now() + timedelta(hours=hours)
    db = await MongoManager.get()
    await db.kv_set("hh_factor", factor)
    await db.kv_set("hh_until", until)
    return until


async def clear_happy_hour() -> None:
    db = await MongoManager.get()
    await db.kv_set("hh_factor", 1.0)
    await db.kv_set("hh_until", None)


async def hh_banner() -> str:
    hh = await happy_hour()
    if not hh["active"]:
        return ""
    off = int(round((1.0 - hh["factor"]) * 100))
    mins = int((hh["until"] - _now()).total_seconds() // 60)
    when = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
    return (f"⚡ <b>Happy Hour</b> · <code>{off}%</code> off every download — "
            f"<i>ends in {when}</i>")


# ── Per-item surge ────────────────────────────────────────────────────────────
async def _surge_settings() -> tuple[bool, float]:
    db = await MongoManager.get()
    on = bool(await db.kv_get("surge_on", False))
    max_pct = float(await db.kv_get("surge_max_pct", 25.0) or 25.0)
    return on, max(0.0, min(200.0, max_pct))


def _surge_tier(dl_count: int, max_pct: float) -> float:
    """Surcharge fraction (0..max_pct/100) by popularity tier."""
    if dl_count >= 200:
        frac = max_pct
    elif dl_count >= 100:
        frac = max_pct * 0.66
    elif dl_count >= 50:
        frac = max_pct * 0.33
    else:
        frac = 0.0
    return frac / 100.0


async def surge_factor(file: dict) -> float:
    on, max_pct = await _surge_settings()
    if not on:
        return 1.0
    dl = int((file or {}).get("dl_count") or 0)
    return round(1.0 + _surge_tier(dl, max_pct), 4)


async def download_multiplier(file: dict) -> float:
    """Combined happy-hour × surge multiplier for one file's download cost."""
    hh = await happy_hour()
    return round(hh["factor"] * await surge_factor(file), 4)
