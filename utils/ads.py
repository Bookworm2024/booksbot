"""
utils/ads.py — in-bot sponsored ad slots (revenue).

Admins sell promo placements: a short message + optional link, weighted so
higher-paying sponsors show more often. One active ad is surfaced on the
dashboard; impressions/clicks are tracked per ad. Stored in Mongo `ads`.
"""
import logging
import random
import string
from datetime import datetime, timezone

from database.connection import MongoManager

logger = logging.getLogger(__name__)

_MAX_ACTIVE_SCAN = 100


def _now():
    return datetime.now(timezone.utc)


def _gen_id() -> str:
    return "ad_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


async def create_ad(text: str, url: str, label: str, weight: int, by: int) -> str:
    db = await MongoManager.get()
    ad_id = _gen_id()
    await db.safe_insert("ads", {
        "ad_id": ad_id, "text": text[:600], "url": (url or "")[:300],
        "label": (label or "📢 Sponsored")[:40], "weight": max(1, min(10, int(weight))),
        "active": True, "impressions": 0, "clicks": 0,
        "created_by": by, "created_at": _now(),
    })
    return ad_id


async def all_ads(limit: int = 25) -> list[dict]:
    from pymongo import DESCENDING
    db = await MongoManager.get()
    return await db.find_global("ads", {}, limit=limit, sort=[("created_at", DESCENDING)])


async def get_ad(ad_id: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("ads", {"ad_id": ad_id})


async def set_active(ad_id: str, on: bool) -> None:
    db = await MongoManager.get()
    await db.safe_update("ads", {"ad_id": ad_id}, {"$set": {"active": bool(on)}}, upsert=False)


async def delete_ad(ad_id: str) -> None:
    db = await MongoManager.get()
    for idx in db.healthy:
        await db.dbs[idx]["ads"].delete_one({"ad_id": ad_id})


async def pick_active() -> dict | None:
    """Weighted-random active ad (bumps its impression count). None if no active
    ads. Best-effort — never raises into the caller (the dashboard)."""
    try:
        db = await MongoManager.get()
        active = await db.find_global("ads", {"active": True}, limit=_MAX_ACTIVE_SCAN)
        if not active:
            return None
        bag = [a for a in active for _ in range(max(1, int(a.get("weight") or 1)))]
        ad = random.choice(bag)
        await db.safe_update("ads", {"ad_id": ad["ad_id"]},
                             {"$inc": {"impressions": 1}}, upsert=False)
        return ad
    except Exception:  # noqa: BLE001
        logger.debug("ads.pick_active failed", exc_info=True)
        return None


async def bump_click(ad_id: str) -> None:
    db = await MongoManager.get()
    await db.safe_update("ads", {"ad_id": ad_id}, {"$inc": {"clicks": 1}}, upsert=False)
