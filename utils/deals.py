"""
utils/deals.py — flash sales / happy-hour bonus events.

An admin fires a timed deal (e.g. "+50% bonus BGM for 6h"). While active, every
BGM purchase gets extra bonus BGM on top of the normal bundle bonus. The deal
amount is locked in at purchase time, so a payment confirming after the deal
ends still honours it. Stored in Mongo kv.
"""
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager


def _now():
    return datetime.now(timezone.utc)


async def get_deal() -> dict:
    db = await MongoManager.get()
    pct = int(await db.kv_get("deal_pct", 0) or 0)
    until = await db.kv_get("deal_until", None)
    active = pct > 0 and isinstance(until, datetime) and until > _now()
    return {"pct": pct if active else 0, "until": until if active else None,
            "active": active}


async def set_deal(pct: int, hours: float) -> datetime:
    db = await MongoManager.get()
    until = _now() + timedelta(hours=hours)
    await db.kv_set("deal_pct", int(pct))
    await db.kv_set("deal_until", until)
    return until


async def clear_deal() -> None:
    db = await MongoManager.get()
    await db.kv_set("deal_pct", 0)
    await db.kv_set("deal_until", None)


async def deal_bonus(bgm: float) -> float:
    """Extra bonus BGM from the active deal (0 if none)."""
    d = await get_deal()
    return round(bgm * d["pct"] / 100, 2) if d["active"] else 0.0


async def banner() -> str:
    d = await get_deal()
    if not d["active"]:
        return ""
    mins = int((d["until"] - _now()).total_seconds() // 60)
    when = f"{mins//60}h {mins%60}m" if mins >= 60 else f"{mins}m"
    return (f"🔥 <b>Flash Sale</b> · <code>+{d['pct']}%</code> bonus 💎 BGM on every top-up — "
            f"<i>ends in {when}</i>")
