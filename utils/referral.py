"""
utils/referral.py — referral attribution & rewards.

Rule (from spec): when a NEW user starts via someone's link AND passes the
channel join-gate, the referrer gets +0.5 BGM and the new user +0.25 BGM,
exactly once.

Attribution is stored at /start (the deep-link payload is the referrer's id);
the reward is granted later, the first time the user clears the join-gate.
"""
import logging

from database.connection import MongoManager
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)

_REF_BONUS = 0.5
_NEW_BONUS = 0.25


async def remember_referrer(uid: int, raw: str) -> None:
    """Record the referrer for a user if valid and not already set."""
    if not raw or not raw.isdigit():
        return
    ref = int(raw)
    if ref == uid:
        return
    db = await MongoManager.get()
    me = await db.find_one_global("users", {"user_id": uid},
                                  {"referrer": 1, "referral_rewarded": 1})
    if me and (me.get("referrer") or me.get("referral_rewarded")):
        return  # already attributed / rewarded
    if not await db.find_one_global("users", {"user_id": ref}, {"_id": 1}):
        return  # referrer must be a known user
    await db.safe_update("users", {"user_id": uid}, {"$set": {"referrer": ref}})


async def grant_referral(bot, uid: int) -> None:
    """Pay out the referral once, the first time `uid` clears the join-gate."""
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid},
                                   {"referrer": 1, "referral_rewarded": 1})
    if not doc or doc.get("referral_rewarded") or not doc.get("referrer"):
        return
    ref = int(doc["referrer"])
    await db.safe_update("users", {"user_id": uid}, {"$set": {"referral_rewarded": True}})
    await add_bgm(uid, _NEW_BONUS)
    await add_bgm(ref, _REF_BONUS)
    await db.safe_update("users", {"user_id": ref}, {"$inc": {"ref_count": 1}})
    try:
        await bot.send_message(uid, f"🎁 <b>Referral Bonus!</b> +{_NEW_BONUS} BGM added.")
    except Exception:  # noqa: BLE001
        pass
    try:
        await bot.send_message(ref, f"🎉 <b>New Referral!</b> You earned +{_REF_BONUS} BGM.")
    except Exception:  # noqa: BLE001
        pass
