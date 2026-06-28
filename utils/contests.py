"""
utils/contests.py — monthly referral contests (Growth).

Each successful referral bumps the referrer's count for the current calendar
month in a dedicated `ref_contest` collection (so history survives the month
rollover, unlike a single reset counter). At month end the top 3 are paid a BGM
prize, settled lazily and exactly once (guarded by a kv flag) the next time the
contest is viewed or a referral is granted — no background worker needed.
"""
import logging
from datetime import datetime, timezone

from pymongo import DESCENDING

from database.connection import MongoManager
from utils.format import fmt_amount
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)

# BGM prize for 1st / 2nd / 3rd place
PRIZES = [25.0, 15.0, 10.0]


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def this_month() -> str:
    return _month_key(datetime.now(timezone.utc))


def prev_month() -> str:
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year}-{month:02d}"


async def bump(uid: int) -> None:
    """Record one referral for `uid` in the current month. Never raises."""
    try:
        db = await MongoManager.get()
        # filter equality (user_id, month) is applied to the new doc on upsert,
        # so no $setOnInsert is needed (and avoids any path-conflict with $inc).
        await db.safe_update("ref_contest", {"user_id": uid, "month": this_month()},
                             {"$inc": {"count": 1}}, upsert=True)
    except Exception:  # noqa: BLE001
        logger.debug("contests.bump failed for %s", uid, exc_info=True)


async def top_month(month: str, limit: int = 10) -> list[dict]:
    db = await MongoManager.get()
    return await db.find_global("ref_contest", {"month": month, "count": {"$gt": 0}},
                                limit=limit, sort=[("count", DESCENDING)])


async def my_stats(uid: int, month: str) -> tuple[int, int | None]:
    """(my_count, my_rank) for the month; rank is None if not participating."""
    db = await MongoManager.get()
    doc = await db.find_one_global("ref_contest", {"user_id": uid, "month": month})
    mine = int((doc or {}).get("count") or 0)
    if mine <= 0:
        return 0, None
    ahead = await db.count_global("ref_contest", {"month": month, "count": {"$gt": mine}})
    return mine, ahead + 1


async def settle(bot) -> None:
    """Pay out the previous month's top 3 exactly once (kv-guarded). Safe to call
    often; a no-op once that month is settled."""
    try:
        db = await MongoManager.get()
        month = prev_month()
        flag = f"refc_settled:{month}"
        # Atomic one-shot claim: only the single caller that flips the flag pays
        # out. A plain kv_get/kv_set guard has a read-then-write window that lets
        # two concurrent settle() calls both pay each winner twice.
        if not await db.kv_claim(flag):
            return
        winners = await top_month(month, len(PRIZES))
        for i, w in enumerate(winners):
            prize = PRIZES[i]
            uid = w.get("user_id")
            if not uid or prize <= 0:
                continue
            await add_bgm(uid, prize)
            place = ["🥇 1st place", "🥈 2nd place", "🥉 3rd place"][i]
            try:
                await bot.send_message(
                    uid,
                    "🏁 <b>Monthly Referral Contest — Results</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>The results for <code>{month}</code> are in — and you made the podium.</i>\n"
                    f"<blockquote>🏆 You finished <b>{place}</b>\n"
                    f"📊 With <code>{int(w.get('count') or 0)}</code> verified referrals\n"
                    f"🎁 Prize: <b>+{fmt_amount(prize)} 💎 BGM</b></blockquote>\n"
                    "<i>💡 A new contest has already begun — your link is ready when you are.</i>")
            except Exception:  # noqa: BLE001
                pass
        if winners:
            logger.info("referral contest %s settled: %d winner(s)", month, len(winners))
    except Exception:  # noqa: BLE001
        logger.debug("contests.settle failed", exc_info=True)
