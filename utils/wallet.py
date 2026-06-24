"""
utils/wallet.py — the token economy.

Two currencies (mirroring the TBC bot):
  • BGM  "bookgem"   — permanent, never expires. Bought, won, redeemed, refunded.
  • BCN  "bookcoin"  — free daily claim, EXPIRES after BCN_EXPIRY_SECONDS (24h).

Cross-cluster correctness
-------------------------
A single user can end up with a doc in more than one cluster (a quota
write-failover, or a credit that upserted on a different write_idx). The old code
read/spent only the FIRST cluster's doc, which split a balance three ways:
  • the displayed balance under-reported  → wrong total
  • spend() tested each bucket alone       → "insufficient" despite enough total
  • convert credited one cluster, read another → conversion looked unreflected

So every read here SUMS across all clusters and every spend COMBINES BCN+BGM
across all clusters. Every amount is run through ``sanitize_amount`` (finite,
non-negative, ≤ MAX_AMOUNT) so a corrupt value can never enter or leave the
wallet — the hard backstop against the balance-explosion ("1e+21") bug.

Two timestamps, not one
-----------------------
``last_claim_at`` gates the 24h daily-claim cooldown; ``bcn_claimed_at`` marks
when the held BCN batch started (drives 24h expiry). They are SEPARATE so that
spending or converting your BCN (which clears the expiry marker) can never reset
the claim cooldown — and a refund of BCN can set a fresh expiry without touching
the cooldown. (Legacy docs only have ``bcn_claimed_at``; the cooldown read falls
back to it so the split is migration-safe.)
"""
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from pymongo import ReturnDocument

from config import BCN_EXPIRY_SECONDS
from database.connection import MongoManager
from utils.format import MAX_AMOUNT, sanitize_amount

logger = logging.getLogger(__name__)

# Float comparison slack so rounding noise never blocks a legitimate spend.
_EPS = 1e-9


def _is_clean(raw) -> bool:
    """True if a stored balance is already a finite number within range — i.e. it
    needs no healing. Strings, NaN/inf, negatives and out-of-range values are
    'unclean' and get rewritten to their sanitized form on access. This matters
    because Mongo range queries ({$gte: n}) are type-bracketed: a string/garbage
    bookgem would pass sanitize() on read yet never match the spend deduction —
    the "I have tons of BGM but it says insufficient" bug."""
    return (isinstance(raw, (int, float)) and not isinstance(raw, bool)
            and math.isfinite(raw) and 0.0 <= raw <= MAX_AMOUNT)


async def _heal(db, user_id: int, fields: tuple[str, ...] = ("bookgem", "bookcoin")) -> None:
    """Rewrite any corrupt stored balance to a clean sanitized float across every
    cluster, so a corrupt value (e.g. a leftover 1e21) self-repairs on first
    access and spend/convert work normally afterward."""
    for idx in db.healthy:
        doc = await db.dbs[idx]["users"].find_one(
            {"user_id": user_id}, {f: 1 for f in fields})
        if not doc:
            continue
        fix = {f: sanitize_amount(doc.get(f)) for f in fields
               if doc.get(f) is not None and not _is_clean(doc.get(f))}
        if fix:
            logger.warning("healing corrupt balance for %s: %s", user_id, fix)
            await db.dbs[idx]["users"].update_one({"user_id": user_id}, {"$set": fix})


class Receipt(str):
    """What spend() returns on success: a normal currency-label string
    ('BCN' / 'BGM' / 'BCN+BGM') that ALSO carries the exact per-bucket charge
    breakdown, so a delivery-failure refund restores each bucket precisely and
    never launders expiring BCN into permanent BGM. It behaves as the label
    string everywhere (display, logging, ``== "BCN"`` branches, Mongo storage)."""

    charges: dict

    def __new__(cls, label: str, charges: Optional[dict] = None) -> "Receipt":
        obj = super().__new__(cls, label)
        obj.charges = dict(charges or {})
        return obj


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _existing_clusters(db, user_id: int) -> list[int]:
    """Indices of every cluster that already holds this user's doc."""
    out: list[int] = []
    for idx in db.healthy:
        if await db.dbs[idx]["users"].find_one({"user_id": user_id}, {"_id": 1}):
            out.append(idx)
    return out


async def check_bcn_expiry(user_id: int) -> None:
    """Zero out expired BCN in EVERY cluster holding the doc. Call before any
    balance read/spend. Uses bcn_claimed_at (the expiry marker), NOT the claim
    cooldown stamp."""
    db = await MongoManager.get()
    now = _now()
    for idx in db.healthy:
        doc = await db.dbs[idx]["users"].find_one(
            {"user_id": user_id}, {"bookcoin": 1, "bcn_claimed_at": 1})
        if not doc:
            continue
        claimed = doc.get("bcn_claimed_at")
        bcn = sanitize_amount(doc.get("bookcoin"))
        if bcn > 0 and claimed:
            age = (now - claimed).total_seconds()
            if age > BCN_EXPIRY_SECONDS:
                await db.dbs[idx]["users"].update_one(
                    {"user_id": user_id},
                    {"$set": {"bookcoin": 0.0, "bcn_claimed_at": None}})


async def get_balances(user_id: int) -> tuple[float, float]:
    """Return (bgm, bcn) SUMMED across all clusters, after applying expiry.
    Self-heals any corrupt stored value in passing so spend always agrees."""
    await check_bcn_expiry(user_id)
    db = await MongoManager.get()
    bgm = bcn = 0.0
    for idx in db.healthy:
        doc = await db.dbs[idx]["users"].find_one(
            {"user_id": user_id}, {"bookgem": 1, "bookcoin": 1})
        if not doc:
            continue
        g, c = doc.get("bookgem"), doc.get("bookcoin")
        gg, cc = sanitize_amount(g), sanitize_amount(c)
        fix = {}
        if g is not None and not _is_clean(g):
            fix["bookgem"] = gg
        if c is not None and not _is_clean(c):
            fix["bookcoin"] = cc
        if fix:
            logger.warning("healing corrupt balance for %s: %s", user_id, fix)
            await db.dbs[idx]["users"].update_one({"user_id": user_id}, {"$set": fix})
        bgm += gg
        bcn += cc
    # A second clamp on the total guarantees no display path ever sees a value
    # outside the legitimate range (defence in depth against corruption).
    return sanitize_amount(bgm), sanitize_amount(bcn)


async def add_bgm(user_id: int, amount: float) -> None:
    """Credit BGM. Amount is sanitized (finite, ≤ MAX_AMOUNT); a non-positive
    amount is a no-op — to DEDUCT use cut_bgm/charge_bgm, never a negative here."""
    amount = sanitize_amount(amount)
    if amount <= 0:
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": user_id}, {"$inc": {"bookgem": amount}})


async def cut_bgm(user_id: int, amount: float) -> None:
    """Deduct BGM, COMBINED across clusters and clamped to the held balance (a
    penalty can never push the wallet negative or overdraw a single cluster).
    Use this, not add_bgm with a negative, for penalties."""
    amount = sanitize_amount(amount)
    if amount <= 0:
        return
    bgm, _ = await get_balances(user_id)
    take = min(amount, bgm)
    if take <= 0:
        return
    await _charge(user_id, take, ("bookgem",))


async def set_bgm(user_id: int, amount: float) -> float:
    """Hard-set bookgem to an exact, sanitized value and COLLAPSE any cross-cluster
    split (the home doc gets the value, any other cluster copies are zeroed). This
    is how an admin repairs a corrupted balance. Returns the value set."""
    amount = sanitize_amount(amount)
    db = await MongoManager.get()
    existing = await _existing_clusters(db, user_id)
    if not existing:
        await db.safe_update("users", {"user_id": user_id}, {"$set": {"bookgem": amount}})
        return amount
    home = existing[0]
    for idx in existing:
        await db.dbs[idx]["users"].update_one(
            {"user_id": user_id}, {"$set": {"bookgem": amount if idx == home else 0.0}})
    return amount


async def set_daily_bcn(user_id: int, amount: float) -> None:
    """Set the daily claim (replaces any leftover), stamp the expiry marker AND
    the claim-cooldown stamp. Writes to a single home doc and zeroes any other
    cluster copies so the summed read can't double-count."""
    amount = sanitize_amount(amount)
    db = await MongoManager.get()
    now = _now()
    existing = await _existing_clusters(db, user_id)
    if not existing:
        await db.safe_update(
            "users", {"user_id": user_id},
            {"$set": {"bookcoin": amount, "bcn_claimed_at": now, "last_claim_at": now}})
        return
    home = existing[0]
    for idx in existing:
        if idx == home:
            await db.dbs[idx]["users"].update_one(
                {"user_id": user_id},
                {"$set": {"bookcoin": amount, "bcn_claimed_at": now, "last_claim_at": now}})
        else:
            await db.dbs[idx]["users"].update_one(
                {"user_id": user_id}, {"$set": {"bookcoin": 0.0, "bcn_claimed_at": None}})


async def seconds_until_claim(user_id: int) -> int:
    """0 == claimable now; else seconds left on the cooldown. Reads the dedicated
    last_claim_at stamp (falling back to legacy bcn_claimed_at), taking the most
    recent across clusters — so spending/converting BCN never opens the cooldown."""
    db = await MongoManager.get()
    latest: Optional[datetime] = None
    for idx in db.healthy:
        doc = await db.dbs[idx]["users"].find_one(
            {"user_id": user_id}, {"last_claim_at": 1, "bcn_claimed_at": 1})
        if not doc:
            continue
        stamp = doc.get("last_claim_at") or doc.get("bcn_claimed_at")
        if stamp and (latest is None or stamp > latest):
            latest = stamp
    if not latest:
        return 0
    age = (_now() - latest).total_seconds()
    return max(0, int(BCN_EXPIRY_SECONDS - age))


async def drain_bcn(user_id: int) -> float:
    """Atomically zero bookcoin in EVERY cluster and return the total drained.
    Used by the BCN→BGM converter so a split BCN balance converts in full. Clears
    only the expiry marker (bcn_claimed_at) — NOT last_claim_at — so converting
    your BCN can never reset the daily-claim cooldown."""
    await check_bcn_expiry(user_id)
    db = await MongoManager.get()
    await _heal(db, user_id, ("bookcoin",))  # so {$gt:0} matches even if stored corrupt
    total = 0.0
    for idx in db.healthy:
        doc = await db.dbs[idx]["users"].find_one_and_update(
            {"user_id": user_id, "bookcoin": {"$gt": 0}},
            {"$set": {"bookcoin": 0.0, "bcn_claimed_at": None}},
            return_document=ReturnDocument.BEFORE)
        if doc:
            total += sanitize_amount(doc.get("bookcoin"))
    return total


async def add_bcn(user_id: int, amount: float) -> None:
    """Credit BCN and (re)start its 24h expiry without touching the claim
    cooldown. Used to restore BCN (e.g. a failed convert/refund)."""
    amount = sanitize_amount(amount)
    if amount <= 0:
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": user_id},
                         {"$inc": {"bookcoin": amount}, "$set": {"bcn_claimed_at": _now()}})


async def _charge(user_id: int, cost: float, fields: tuple[str, ...]) -> Optional[dict]:
    """Deduct `cost` from the given balance fields (in order), COMBINING the same
    field split across clusters and rolling from one field into the next. Returns
    a {field: amount_charged} dict on success ({} when cost<=0), or None if the
    user can't afford it.

    A per-doc conditional update guards against overdraft under concurrency; if a
    race leaves the deduction partial we credit back what we took and deny, so the
    wallet is never left short and never overdrawn.
    """
    db = await MongoManager.get()
    cost = sanitize_amount(cost)
    if cost <= 0:
        return {}

    rows: list[tuple[int, dict[str, float]]] = []
    total = 0.0
    proj = {f: 1 for f in fields}
    for idx in db.healthy:
        doc = await db.dbs[idx]["users"].find_one({"user_id": user_id}, proj)
        if not doc:
            continue
        bal, fix = {}, {}
        for f in fields:
            raw = doc.get(f)
            clean = sanitize_amount(raw)
            bal[f] = clean
            if raw is not None and not _is_clean(raw):
                fix[f] = clean
        if fix:
            # rewrite corrupt values to clean floats so the conditional deduction
            # below ({$gte}) actually matches — fixes "enough BGM but insufficient".
            logger.warning("healing corrupt balance for %s: %s", user_id, fix)
            await db.dbs[idx]["users"].update_one({"user_id": user_id}, {"$set": fix})
        rows.append((idx, bal))
        total += sum(bal.values())
    if total + _EPS < cost:
        return None

    remaining = cost
    charged: dict[str, float] = {f: 0.0 for f in fields}
    for field in fields:
        for idx, bal in rows:
            if remaining <= _EPS:
                break
            take = min(bal[field], remaining)
            if take <= _EPS:
                continue
            res = await db.dbs[idx]["users"].update_one(
                {"user_id": user_id, field: {"$gte": take - _EPS}},
                {"$inc": {field: -take}})
            if res.modified_count:
                remaining = round(remaining - take, 9)
                charged[field] += take
        if remaining <= _EPS:
            break

    if remaining > _EPS:
        # couldn't fully cover (lost a race) → restore what we took and deny
        for field, amt in charged.items():
            if amt > _EPS:
                await db.safe_update("users", {"user_id": user_id},
                                     {"$inc": {field: amt}}, upsert=False)
        return None
    return {f: round(amt, 6) for f, amt in charged.items() if amt > _EPS}


async def spend(user_id: int, cost: float) -> Optional[Receipt]:
    """Deduct `cost`, BCN-first then BGM, COMBINING both buckets across all
    clusters. Returns a Receipt (str 'BCN'/'BGM'/'BCN+BGM' carrying the exact
    per-bucket breakdown) on success, or None if the user can't afford it."""
    await check_bcn_expiry(user_id)
    charged = await _charge(user_id, cost, ("bookcoin", "bookgem"))
    if charged is None:
        return None
    if not charged:                         # cost <= 0 → free
        return Receipt("BGM", {})
    used = set(charged)
    if used == {"bookcoin"}:
        label = "BCN"
    elif used == {"bookgem"}:
        label = "BGM"
    else:
        label = "BCN+BGM"
    return Receipt(label, charged)


async def charge_bgm(user_id: int, cost: float) -> bool:
    """Spend BGM only (permanent currency), combined across clusters. Used by
    BGM-priced features (cosmetics, vanity, VIP, gifting). True on success."""
    return await _charge(user_id, cost, ("bookgem",)) is not None


async def refund(user_id: int, amount: float, currency) -> None:
    """Refund a prior spend. When `currency` is a Receipt from spend(), each bucket
    is restored EXACTLY (so a failed delivery never converts BCN into BGM); else
    `amount` is credited to BCN or BGM by the label string."""
    db = await MongoManager.get()
    charges = getattr(currency, "charges", None)
    if charges:
        for field, amt in charges.items():
            amt = sanitize_amount(amt)
            if amt <= 0:
                continue
            await db.safe_update("users", {"user_id": user_id},
                                 {"$inc": {field: amt}}, upsert=False)
            if field == "bookcoin":
                await _restamp_refunded_bcn(db, user_id)
        return
    amount = sanitize_amount(amount)
    if amount <= 0:
        return
    field = "bookcoin" if currency == "BCN" else "bookgem"
    await db.safe_update("users", {"user_id": user_id}, {"$inc": {field: amount}})
    if field == "bookcoin":
        await _restamp_refunded_bcn(db, user_id)


async def _restamp_refunded_bcn(db, user_id: int) -> None:
    """Give refunded BCN a defined 24h expiry (set bcn_claimed_at where it's
    missing) WITHOUT touching last_claim_at — so a refund never makes BCN immortal
    nor resets the claim cooldown."""
    for idx in db.healthy:
        await db.dbs[idx]["users"].update_one(
            {"user_id": user_id, "bcn_claimed_at": None, "bookcoin": {"$gt": 0}},
            {"$set": {"bcn_claimed_at": _now()}})
