"""
utils/permissions.py — admin roles & granular per-admin permissions.

TWO ROLES, clearly separated:

  • SUPER ADMIN (config.SUPER_ADMIN_ID) — the owner. Holds EVERY capability and
    can never be restricted. Only the super admin can touch anything that affects
    money, reputation, branding or the whole user base: payments / revenue,
    pricing & deals, broadcasting, the BGM economy (add / set / bulk / codes),
    maintenance mode, featured placements, ads, the file channel, AI settings,
    backups, GDPR and admin management.

  • NORMAL ADMIN (config.ADMIN_IDS) — a helper. Can ONLY do the safe, day-to-day
    tasks the super admin explicitly delegates. By default a brand-new normal
    admin can do exactly one thing: handle book requests / send files. The super
    admin can grant a few more *delegatable* capabilities per-person from
    🔑 Permissions, but the sensitive owner-only tools above are NEVER delegatable.

This is least-privilege by design. An admin with no explicit permission record
gets DEFAULT_ADMIN_PERMS (not full access) — so adding a helper never exposes
payments, broadcasts or the economy. Stored in kv `admin_perms`:
    { "<uid>": ["requests", "moderation", ...] }   # delegatable keys only
"""
import config
from config import SUPER_ADMIN_ID
from database.connection import MongoManager

# Delegatable capabilities — the ONLY things a normal admin can ever be granted.
# key → human label (shown in the 🔑 Permissions panel).
PERMS = {
    "requests":   "📬 Handle Requests / Send Books",
    "moderation": "🛡 Reports & Risk Review",
    "content":    "🗂 Add Files & Questions",
    "ban":        "🚫 Ban / Unban Users",
}

# The set of keys that may be toggled for a normal admin.
DELEGATABLE = set(PERMS)

# What a brand-new normal admin can do until the super admin grants more.
# Least-privilege: just fulfil book requests. Everything else is opt-in.
DEFAULT_ADMIN_PERMS = {"requests"}


def is_super(uid: int) -> bool:
    """True only for the owner / super admin."""
    return uid == SUPER_ADMIN_ID


def is_admin(uid: int) -> bool:
    """True for the super admin or any configured normal admin."""
    return uid == SUPER_ADMIN_ID or uid in config.ADMIN_IDS


async def _stored() -> dict:
    db = await MongoManager.get()
    return await db.kv_get("admin_perms", {}) or {}


async def perms_for(uid: int) -> set[str]:
    """The delegatable capabilities this admin currently holds.

    Super admin → all delegatable keys (shown for completeness).
    Normal admin with no record → DEFAULT_ADMIN_PERMS (least-privilege).
    Normal admin with a record → exactly what was granted (∩ delegatable).
    """
    if uid == SUPER_ADMIN_ID:
        return set(PERMS)
    stored = await _stored()
    rec = stored.get(str(uid))
    if rec is None:
        return set(DEFAULT_ADMIN_PERMS)   # least-privilege by default
    return {p for p in rec if p in DELEGATABLE}


async def has(uid: int, perm: str) -> bool:
    """True if `uid` may use capability `perm`.

    The super admin always passes. A normal admin passes only for a DELEGATABLE
    capability they've been granted — any owner-only capability (broadcast,
    payments, economy, pricing, branding, …) returns False for everyone but the
    super admin, even if some caller passes its key here.
    """
    if uid == SUPER_ADMIN_ID:
        return True
    if uid not in config.ADMIN_IDS:
        return False
    if perm not in DELEGATABLE:
        return False                       # owner-only capability
    return perm in await perms_for(uid)


async def set_perms(uid: int, perms: list[str]) -> None:
    """Persist an admin's delegatable permissions (non-delegatable keys dropped)."""
    db = await MongoManager.get()
    stored = await _stored()
    stored[str(uid)] = sorted({p for p in perms if p in DELEGATABLE})
    await db.kv_set("admin_perms", stored)


async def toggle(uid: int, perm: str) -> bool:
    """Flip one delegatable permission for an admin. Returns the new state
    (True=granted). Non-delegatable keys are ignored (return False)."""
    if perm not in DELEGATABLE:
        return False
    current = await perms_for(uid)
    if perm in current:
        current.discard(perm)
    else:
        current.add(perm)
    await set_perms(uid, list(current))
    return perm in current
