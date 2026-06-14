"""
handlers/admin_api.py — JSON for the admin Mini-App dashboard.

Double-gated: valid Telegram initData AND the user must be in ADMIN_IDS.
Aggregates users / archive / requests / economy / revenue across clusters.
"""
import logging
from datetime import datetime, timezone

from aiohttp import web

from config import ADMIN_IDS
from database.connection import MongoManager
from utils.webapp_auth import user_id_from

logger = logging.getLogger(__name__)


async def _sum(db, coll: str, field: str, flt: dict | None = None) -> float:
    total = 0.0
    match = [{"$match": flt}] if flt else []
    for idx in db.healthy:
        cur = db.dbs[idx][coll].aggregate(
            match + [{"$group": {"_id": None, "t": {"$sum": f"${field}"}}}])
        async for row in cur:
            total += float(row.get("t") or 0)
    return total


def _sod():
    n = datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


async def api_admin_overview(request: web.Request) -> web.Response:
    uid = user_id_from(request.query.get("init_data", ""))
    if not uid:
        return web.json_response({"error": "auth_failed"}, status=401)
    if uid not in ADMIN_IDS:
        return web.json_response({"error": "forbidden"}, status=403)

    db = await MongoManager.get()
    sod = _sod()
    users = await db.count_global("users")
    files = await db.count_global("files")
    new_today = await db.count_global("users", {"joined_at": {"$gte": sod}})
    downloads = await _sum(db, "users", "downloads")
    bgm = await _sum(db, "users", "bookgem")
    bcn = await _sum(db, "users", "bookcoin")
    pend = await db.count_global("requests", {"status": "pending"})
    done = await db.count_global("requests", {"status": "fulfilled"})
    canc = await db.count_global("requests", {"status": "cancelled"})
    inr = await _sum(db, "payments", "total_due_inr", {"status": "paid"})
    usd = await _sum(db, "crypto_orders", "amount_usd", {"status": "paid"})
    inr_today = await _sum(db, "payments", "total_due_inr",
                           {"status": "paid", "paid_at": {"$gte": sod}})
    usd_today = await _sum(db, "crypto_orders", "amount_usd",
                           {"status": "paid", "paid_at": {"$gte": sod}})
    vip = await db.count_global("users", {"vip_until": {"$gt": datetime.now(timezone.utc)}})
    maintenance = bool(await db.kv_get("maintenance", False))

    return web.json_response({
        "users": users, "new_today": new_today, "files": files,
        "downloads": int(downloads), "vip": vip,
        "bgm": round(bgm, 2), "bcn": round(bcn, 2),
        "requests": {"pending": pend, "fulfilled": done, "cancelled": canc},
        "revenue": {"inr": round(inr, 2), "usd": round(usd, 2),
                    "inr_today": round(inr_today, 2), "usd_today": round(usd_today, 2),
                    "gross_inr": round(inr + usd * 85, 0)},
        "maintenance": maintenance,
    })
