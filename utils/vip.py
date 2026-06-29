"""
utils/vip.py — premium subscription tiers.

Buying VIP spends BGM (a healthy sink, since BGM is bought with real money) for
30 days of perks: cheaper/free downloads, a bigger daily claim, and a monthly
BGM grant. Re-subscribing extends the window. Two tiers: Silver, Gold.

User doc fields: vip_tier (0/1/2), vip_until (datetime).
"""
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager
from utils.format import fmt_amount
from utils.wallet import add_bgm, charge_bgm

# Single freemium "Premium" tier (id 1). Tier 2 is kept only so any legacy Gold
# holder still reads as active Premium; new purchases always grant tier 1.
# dl_discount is vestigial (downloads are quota-gated now, not BGM-priced);
# claim_mult still gives Premium members a bigger daily BGM claim.
TIERS = {
    1: {"name": "Premium", "emoji": "👑", "price": 0, "days": 30,
        "dl_discount": 1.0, "claim_mult": 2.0, "monthly_bgm": 0},
    2: {"name": "Premium+", "emoji": "👑", "price": 0, "days": 30,
        "dl_discount": 1.0, "claim_mult": 2.0, "monthly_bgm": 0},
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
        return False, "👑 That membership tier isn't available. Choose Silver or Gold to continue."
    db = await MongoManager.get()
    # BGM debit, combined across clusters (no false "insufficient" on a split
    # balance); rolls back a partial debit on failure.
    if not await charge_bgm(uid, cfg["price"]):
        return False, (f"💎 {cfg['name']} costs {fmt_amount(cfg['price'])} BGM, and your wallet's a "
                       f"little short right now. Top up your BGM and your membership will be ready and waiting.")
    doc = await db.find_one_global("users", {"user_id": uid}, {"vip_until": 1}) or {}
    cur_until = doc.get("vip_until")
    base = cur_until if isinstance(cur_until, datetime) and cur_until > _now() else _now()
    new_until = base + timedelta(days=cfg["days"])
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"vip_tier": tier, "vip_until": new_until}})
    if cfg["monthly_bgm"]:
        await add_bgm(uid, cfg["monthly_bgm"])
    return True, (f"{cfg['emoji']} <b>Welcome to {cfg['name']}</b>\n"
                  f"<i>Your membership is live — enjoy every perk.</i>\n"
                  f"<blockquote>🗓 Active through <b>{new_until.strftime('%d %b %Y')}</b>\n"
                  f"🎁 We've credited <code>{fmt_amount(cfg['monthly_bgm'])} BGM</code> to your wallet to celebrate.</blockquote>\n"
                  f"<i>💡 Re-subscribe any time to extend the window — perks pick up right where they left off.</i>")


async def badge(uid: int) -> str:
    st = await get_status(uid)
    if not st["active"]:
        return ""
    cfg = TIERS[st["tier"]]
    return f"{cfg['emoji']} {cfg['name']}"
