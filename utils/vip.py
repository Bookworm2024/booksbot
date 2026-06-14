"""
utils/vip.py — premium subscription tiers.

Buying VIP spends BGM (a healthy sink, since BGM is bought with real money) for
30 days of perks: cheaper/free downloads, a bigger daily claim, and a monthly
BGM grant. Re-subscribing extends the window. Two tiers: Silver, Gold.

User doc fields: vip_tier (0/1/2), vip_until (datetime).
"""
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager
from utils.wallet import add_bgm

TIERS = {
    1: {"name": "Silver VIP", "emoji": "🥈", "price": 50, "days": 30,
        "dl_discount": 0.5, "claim_mult": 1.5, "monthly_bgm": 10},
    2: {"name": "Gold VIP", "emoji": "🥇", "price": 120, "days": 30,
        "dl_discount": 1.0, "claim_mult": 2.0, "monthly_bgm": 30},
}


def _now():
    return datetime.now(timezone.utc)


async def get_status(uid: int) -> dict:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid},
                                   {"vip_tier": 1, "vip_until": 1}) or {}
    tier = int(doc.get("vip_tier") or 0)
    until = doc.get("vip_until")
    active = tier > 0 and isinstance(until, datetime) and until > _now()
    return {"tier": tier if active else 0, "active": active,
            "until": until if active else None}


async def perks(uid: int) -> dict | None:
    st = await get_status(uid)
    return TIERS.get(st["tier"]) if st["active"] else None


async def download_factor(uid: int) -> float:
    """Multiplier on download cost (0 = free for the user)."""
    p = await perks(uid)
    return (1.0 - p["dl_discount"]) if p else 1.0


async def claim_multiplier(uid: int) -> float:
    p = await perks(uid)
    return p["claim_mult"] if p else 1.0


async def subscribe(uid: int, tier: int) -> tuple[bool, str]:
    cfg = TIERS.get(tier)
    if not cfg:
        return False, "Unknown tier."
    db = await MongoManager.get()
    # atomic BGM debit (only if they hold the price)
    debit = await db.find_one_and_update_global(
        "users", {"user_id": uid, "bookgem": {"$gte": cfg["price"]}},
        {"$inc": {"bookgem": -cfg["price"]}})
    if not debit:
        return False, f"You need {cfg['price']} BGM for {cfg['name']}."
    cur_until = debit.get("vip_until")
    base = cur_until if isinstance(cur_until, datetime) and cur_until > _now() else _now()
    new_until = base + timedelta(days=cfg["days"])
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"vip_tier": tier, "vip_until": new_until}})
    if cfg["monthly_bgm"]:
        await add_bgm(uid, cfg["monthly_bgm"])
    return True, (f"{cfg['emoji']} <b>{cfg['name']} activated!</b>\n"
                  f"Valid until {new_until.strftime('%d %b %Y')}.\n"
                  f"🎁 +{cfg['monthly_bgm']} BGM granted now.")


async def badge(uid: int) -> str:
    st = await get_status(uid)
    if not st["active"]:
        return ""
    cfg = TIERS[st["tier"]]
    return f"{cfg['emoji']} {cfg['name']}"
