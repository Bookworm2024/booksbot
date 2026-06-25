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
from utils.format import fmt_amount
from utils.settings import get_float
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)

# Defaults live in utils.settings (admin-editable, no redeploy):
#   referrer_bonus (0.5) · referee_bonus (0.25)
# extra one-time bonus when the referrer's count reaches a milestone
_MILESTONES = {5: 2.0, 10: 5.0, 25: 15.0, 50: 40.0, 100: 100.0}


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
    ref_bonus = await get_float("referrer_bonus")
    new_bonus = await get_float("referee_bonus")
    await db.safe_update("users", {"user_id": uid}, {"$set": {"referral_rewarded": True}})
    await add_bgm(uid, new_bonus)
    await add_bgm(ref, ref_bonus)
    # global XP for the referrer (the referred user earns XP through their own
    # actions; the referrer's reward for growing the bot is the XP here)
    from utils.xp import award
    await award(ref, "referral")
    # atomic increment returns the new count → check milestone exactly once
    updated = await db.find_one_and_update_global(
        "users", {"user_id": ref}, {"$inc": {"ref_count": 1}})
    new_count = int((updated or {}).get("ref_count") or 0)
    try:
        await bot.send_message(uid, f"🎁 <b>Referral Bonus!</b> +{fmt_amount(new_bonus)} BGM added.")
    except Exception:  # noqa: BLE001
        pass
    try:
        await bot.send_message(ref, f"🎉 <b>New Referral!</b> You earned +{fmt_amount(ref_bonus)} BGM "
                                    f"(total {new_count}).")
    except Exception:  # noqa: BLE001
        pass
    # milestone bonus
    bonus = _MILESTONES.get(new_count)
    if bonus:
        await add_bgm(ref, bonus)
        try:
            await bot.send_message(
                ref, f"🏆 <b>Milestone!</b> {new_count} referrals → <b>+{fmt_amount(bonus)} BGM</b> bonus!")
        except Exception:  # noqa: BLE001
            pass
