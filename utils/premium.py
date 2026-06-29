"""
utils/premium.py — the freemium "Premium" tier.

One paid tier sits on top of the free experience. It reuses the existing
``vip_tier`` / ``vip_until`` user fields (so legacy VIP holders keep their perks
and nothing has to be migrated) but presents a single, clean "Premium" concept:

    is_premium(uid)        → bool, the gate every freemium check calls
    grant_premium(uid, d)  → extend the window by d days (additive, like VIP)

Premium can be obtained three ways, all funnelled through here so pricing lives
in one place (and stays admin-tunable via utils.settings):
    • wallet ₹  (premium_price_inr, default ₹280 / 30d)
    • wallet $  (premium_price_usd, default $3 / 30d)
    • BGM       (premium_bgm_cost, default 1000 BGM → premium_bgm_days, 7d)

Per-file overage (when a free user is past their daily archive quota) is priced
here too: overage_price_inr (₹100) / overage_price_usd ($2).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from database.connection import MongoManager
from utils.settings import get_float

PREMIUM_TIER = 1  # the tier id a new Premium purchase grants


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── status ──────────────────────────────────────────────────────────────────────
async def status(uid: int) -> dict:
    """{'active': bool, 'until': datetime|None}. Any active vip_tier>0 counts as
    Premium (legacy Silver/Gold holders included)."""
    from utils.vip import get_status
    return await get_status(uid)


async def is_premium(uid: int) -> bool:
    return (await status(uid))["active"]


async def grant_premium(uid: int, days: float) -> datetime:
    """Extend the Premium window by `days` (additive if still active), set tier.
    Returns the new expiry."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"vip_until": 1}) or {}
    cur = doc.get("vip_until")
    base = cur if isinstance(cur, datetime) and cur > _now() else _now()
    new_until = base + timedelta(days=float(days))
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"vip_tier": PREMIUM_TIER, "vip_until": new_until}})
    return new_until


# ── pricing (all live-editable) ──────────────────────────────────────────────────
async def price_inr() -> float:
    return await get_float("premium_price_inr")


async def price_usd() -> float:
    return await get_float("premium_price_usd")


async def money_days() -> int:
    return int(await get_float("premium_days"))


async def bgm_cost() -> float:
    return await get_float("premium_bgm_cost")


async def bgm_days() -> int:
    return int(await get_float("premium_bgm_days"))


async def overage_inr() -> float:
    return await get_float("overage_price_inr")


async def overage_usd() -> float:
    return await get_float("overage_price_usd")


# ── purchase paths ────────────────────────────────────────────────────────────────
async def buy_with_wallet(uid: int, currency: str) -> tuple[bool, Optional[datetime]]:
    """Buy Premium from the real-money wallet. currency: 'inr' | 'usd'.
    Returns (ok, new_until). ok=False means insufficient wallet balance.
    Charge + grant are all-or-nothing: if the grant fails the money is refunded."""
    from utils.wallet import spend_money, add_money
    field = "wallet_inr" if currency == "inr" else "wallet_usd"
    if currency not in ("inr", "usd"):
        raise ValueError(f"bad currency {currency!r}")
    price = await (price_inr() if currency == "inr" else price_usd())
    if not await spend_money(uid, field, price):
        return False, None
    try:
        return True, await grant_premium(uid, await money_days())
    except Exception:  # noqa: BLE001 — never charge without granting
        await add_money(uid, field, price)
        raise


async def redeem_with_bgm(uid: int) -> tuple[bool, Optional[datetime]]:
    """Exchange BGM for a Premium week (default 1000 BGM → 7d). Returns
    (ok, new_until); ok=False means not enough BGM. Refunds BGM if the grant fails."""
    from utils.wallet import charge_bgm, add_bgm
    cost = await bgm_cost()
    if not await charge_bgm(uid, cost):
        return False, None
    try:
        return True, await grant_premium(uid, await bgm_days())
    except Exception:  # noqa: BLE001 — never charge without granting
        await add_bgm(uid, cost)
        raise
