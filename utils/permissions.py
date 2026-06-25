"""
utils/permissions.py — granular per-admin permissions.

Normal admins can be restricted to a subset of capabilities. Backward-compatible
by design: an admin with NO explicit permission record has FULL access (so
nothing changes until the super admin restricts someone). The super admin always
has every permission and can't be restricted.

Stored in kv `admin_perms`: { "<uid>": ["broadcast", "ban", ...] }.
"""
import config
from config import SUPER_ADMIN_ID
from database.connection import MongoManager

# permission key → human label (shown in the panel)
PERMS = {
    "broadcast": "📡 Broadcast",
    "ban":       "🚫 Ban / Unban",
    "requests":  "📬 Handle Requests",
    "users":     "👤 User Mgmt (BGM/lookup)",
    "content":   "🗂 Content / Files",
    "moderation": "🛡 Moderation / Reports",
}


async def _stored() -> dict:
    db = await MongoManager.get()
    return await db.kv_get("admin_perms", {}) or {}


async def perms_for(uid: int) -> set[str]:
    """The set of permission keys this admin holds. Super admin → all; an admin
    with no explicit record → all (full access by default)."""
    if uid == SUPER_ADMIN_ID:
        return set(PERMS)
    stored = await _stored()
    rec = stored.get(str(uid))
    if rec is None:
        return set(PERMS)  # no restriction set → full access (backward compatible)
    return {p for p in rec if p in PERMS}


async def has(uid: int, perm: str) -> bool:
    """True if `uid` is an admin AND holds `perm`. Super admin always True."""
    if uid == SUPER_ADMIN_ID:
        return True
    if uid not in config.ADMIN_IDS:
        return False
    return perm in await perms_for(uid)


async def set_perms(uid: int, perms: list[str]) -> None:
    db = await MongoManager.get()
    stored = await _stored()
    stored[str(uid)] = sorted({p for p in perms if p in PERMS})
    await db.kv_set("admin_perms", stored)


async def toggle(uid: int, perm: str) -> bool:
    """Flip one permission for an admin. Returns the new state (True=granted)."""
    if perm not in PERMS:
        return False
    current = await perms_for(uid)
    if perm in current:
        current.discard(perm)
    else:
        current.add(perm)
    await set_perms(uid, list(current))
    return perm in current
