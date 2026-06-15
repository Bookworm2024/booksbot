"""
handlers/admin_api.py — JSON for the admin Mini-App dashboard.

Double-gated: valid Telegram initData AND the user must be in ADMIN_IDS.
Aggregates users / archive / requests / economy / revenue across clusters.
"""
import logging
from datetime import datetime, timezone

from aiohttp import web

from config import ADMIN_IDS, SUPER_ADMIN_ID
from database.connection import MongoManager
from utils.ai import ai_complete, get_ai_config, set_ai_config
from utils.webapp_auth import user_id_from

logger = logging.getLogger(__name__)


async def _admin_uid(request: web.Request) -> int | None:
    """Resolve+authorise the caller from initData (query for GET, body for POST).
    Returns the admin uid, or None (caller should 401/403)."""
    init = request.query.get("init_data", "")
    body = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        init = body.get("init_data", init)
    request["_body"] = body
    uid = user_id_from(init)
    if not uid or uid not in ADMIN_IDS:
        return None
    return uid


def _mask(s: str) -> str:
    if not s:
        return ""
    return f"{s[:4]}…{s[-3:]}" if len(s) > 10 else "set"


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


# ── AI provider config (super-admin only) ───────────────────────────────────────
async def api_admin_ai(request: web.Request) -> web.Response:
    """GET → current AI config (key masked). POST → update fields."""
    uid = await _admin_uid(request)
    if not uid:
        return web.json_response({"error": "forbidden"}, status=403)

    if request.method == "POST":
        if uid != SUPER_ADMIN_ID:
            return web.json_response({"error": "super_admin_only"}, status=403)
        body = request.get("_body") or {}
        if body.get("provider") in ("free", "anthropic", "off"):
            await set_ai_config("provider", body["provider"])
        if isinstance(body.get("free_url"), str) and body["free_url"].strip():
            await set_ai_config("free_url", body["free_url"].strip())
        # only overwrite the key if a non-empty, non-mask value was sent
        ak = body.get("anthropic_key")
        if isinstance(ak, str) and ak.strip() and "…" not in ak:
            await set_ai_config("anthropic_key", ak.strip())
        if isinstance(body.get("model"), str) and body["model"].strip():
            await set_ai_config("model", body["model"].strip())

    cfg = await get_ai_config()
    return web.json_response({
        "provider": cfg["provider"],
        "free_url": cfg["free_url"],
        "model": cfg["model"],
        "anthropic_key_masked": _mask(cfg["anthropic_key"]),
        "has_key": bool(cfg["anthropic_key"]),
        "can_edit": uid == SUPER_ADMIN_ID,
    })


async def api_admin_ai_test(request: web.Request) -> web.Response:
    """POST → run a tiny live completion through the current provider."""
    uid = await _admin_uid(request)
    if not uid:
        return web.json_response({"error": "forbidden"}, status=403)
    out = await ai_complete("Reply with exactly: PONG", max_tokens=20)
    return web.json_response({"ok": bool(out), "sample": (out or "")[:200]})
