"""
utils/admins.py — runtime admin roster.

ADMIN_IDS (config) holds the env-configured admins (+ the super admin). This
module lets the super admin add/remove EXTRA admins live from /admin, with no
redeploy. Extra admins persist in Mongo `kv` ("extra_admins") and are merged
into the in-memory config.ADMIN_IDS list at startup.

IMPORTANT: we mutate config.ADMIN_IDS *in place* (append/remove) so every module
that did `from config import ADMIN_IDS` sees the change — they share the same
list object. Never rebind config.ADMIN_IDS.
"""
import config
from config import SUPER_ADMIN_ID
from database.connection import MongoManager


async def get_extra_admins() -> list[int]:
    db = await MongoManager.get()
    raw = await db.kv_get("extra_admins", []) or []
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (ValueError, TypeError):
            continue
    return out


async def load_extra_admins() -> None:
    """Merge kv-stored extra admins into the live ADMIN_IDS list (call at startup)."""
    for uid in await get_extra_admins():
        if uid not in config.ADMIN_IDS:
            config.ADMIN_IDS.append(uid)


async def add_admin(uid: int) -> bool:
    """Promote a user to admin. Returns False if they were already an admin."""
    db = await MongoManager.get()
    extra = await get_extra_admins()
    already = uid in config.ADMIN_IDS
    if uid not in extra:
        extra.append(uid)
        await db.kv_set("extra_admins", extra)
    if uid not in config.ADMIN_IDS:
        config.ADMIN_IDS.append(uid)
    return not already


async def remove_admin(uid: int) -> bool:
    """Demote a dynamically-added admin. Env admins and the super admin can't be
    removed here (they're fixed in config). Returns True if removed."""
    if uid == SUPER_ADMIN_ID:
        return False
    extra = await get_extra_admins()
    if uid not in extra:
        return False  # not a dynamically-added admin → not removable here
    db = await MongoManager.get()
    extra = [x for x in extra if x != uid]
    await db.kv_set("extra_admins", extra)
    if uid in config.ADMIN_IDS:
        try:
            config.ADMIN_IDS.remove(uid)
        except ValueError:
            pass
    return True
