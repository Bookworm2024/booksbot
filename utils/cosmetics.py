"""
utils/cosmetics.py — buy-with-BGM profile flair (a cosmetic BGM sink).

Flairs are purely decorative emblems shown on the player profile. Buying is
ATOMIC (a single conditional update deducts BGM and grants the item only if the
user can afford it and doesn't already own it — no double-charge, no double-own).
Owned ids live on users.cosmetics; the equipped one on users.equipped_flair(_id).
"""
from database.connection import MongoManager
from utils.wallet import charge_bgm

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
    """Buy a flair. Returns (ok, reason): reason in
    'ok'/'unknown'/'free'/'owned'/'insufficient'.

    Atomicity: the ownership grant is the gate. We $addToSet the flair only if it
    isn't already owned (a single conditional update — a concurrent double-tap
    matches `cosmetics:{$ne:fid}` once, so only ONE tap claims it). Only the
    winning tap then charges BGM (combined across clusters); if the charge fails,
    we roll the grant back. This restores the single-statement guarantee against
    double-charge while keeping the cross-cluster spend fix."""
    item = by_id(fid)
    if not item:
        return False, "unknown"
    if item["price"] <= 0:
        return False, "free"
    db = await MongoManager.get()
    # atomically claim ownership in whichever cluster holds the user's doc
    claimed = False
    for idx in db.healthy:
        res = await db.dbs[idx]["users"].update_one(
            {"user_id": uid, "cosmetics": {"$ne": fid}},
            {"$addToSet": {"cosmetics": fid}})
        if res.modified_count:
            claimed = True
            break
    if not claimed:
        # already owned (somewhere), or the user has no doc yet
        return False, ("owned" if fid in await owned(uid) else "insufficient")
    if not await charge_bgm(uid, float(item["price"])):
        await db.safe_update("users", {"user_id": uid},
                             {"$pull": {"cosmetics": fid}}, upsert=False)
        return False, "insufficient"
    return True, "ok"


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
