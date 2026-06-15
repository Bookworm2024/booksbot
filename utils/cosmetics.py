"""
utils/cosmetics.py — buy-with-BGM profile flair (a cosmetic BGM sink).

Flairs are purely decorative emblems shown on the player profile. Buying is
ATOMIC (a single conditional update deducts BGM and grants the item only if the
user can afford it and doesn't already own it — no double-charge, no double-own).
Owned ids live on users.cosmetics; the equipped one on users.equipped_flair(_id).
"""
from database.connection import MongoManager

# id · shop label · price (BGM) · flair emblem shown on the profile
FLAIRS = [
    {"id": "none",   "label": "🚫 None",            "price": 0,   "flair": ""},
    {"id": "reader", "label": "📖 Bookworm",        "price": 5,   "flair": "📖"},
    {"id": "star",   "label": "🌟 Star Reader",     "price": 15,  "flair": "🌟"},
    {"id": "fire",   "label": "🔥 On Fire",         "price": 30,  "flair": "🔥"},
    {"id": "crown",  "label": "👑 Royal Reader",    "price": 60,  "flair": "👑"},
    {"id": "dragon", "label": "🐉 Bibliodragon",    "price": 120, "flair": "🐉"},
    {"id": "galaxy", "label": "🌌 Cosmic Reader",   "price": 250, "flair": "🌌"},
]
_BY_ID = {f["id"]: f for f in FLAIRS}


def by_id(fid: str) -> dict | None:
    return _BY_ID.get(fid)


async def owned(uid: int) -> set:
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": uid}, {"cosmetics": 1}) or {}
    return set(d.get("cosmetics") or []) | {"none"}   # 'none' is always available


async def buy(uid: int, fid: str) -> tuple[bool, str]:
    """Atomically buy a flair. Returns (ok, reason): reason in
    'ok'/'unknown'/'free'/'owned'/'insufficient'."""
    item = by_id(fid)
    if not item:
        return False, "unknown"
    if item["price"] <= 0:
        return False, "free"
    db = await MongoManager.get()
    price = float(item["price"])
    # atomic: only deduct + grant if affordable AND not already owned
    for idx in db.healthy:
        res = await db.dbs[idx]["users"].update_one(
            {"user_id": uid, "bookgem": {"$gte": price}, "cosmetics": {"$ne": fid}},
            {"$inc": {"bookgem": -price}, "$addToSet": {"cosmetics": fid}})
        if res.modified_count:
            return True, "ok"
    # no update happened → figure out why
    doc = await db.find_one_global("users", {"user_id": uid}, {"cosmetics": 1}) or {}
    if fid in (doc.get("cosmetics") or []):
        return False, "owned"
    return False, "insufficient"


async def equip(uid: int, fid: str) -> bool:
    item = by_id(fid)
    if not item:
        return False
    if fid != "none" and fid not in await owned(uid):
        return False
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"equipped_flair": item["flair"], "equipped_flair_id": fid}})
    return True
