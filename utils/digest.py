"""
utils/digest.py — weekly personalized digest (retention).

Background loop: once a week per user, DM opted-in, recently-active users a short
digest — new arrivals this week, the Book of the Day, and their streak nudge.
Global content is computed once per tick; per-user cost is just the send + a
last_digest stamp, so it scales. Rate-limited; respects the notif toggle.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)

_INTERVAL = 3600       # check hourly; each user gets at most one digest / 7 days
_PER_TICK = 200        # cap sends per tick (flood safety)
_SLEEP = 0.05          # ~20 msgs/sec
_WEEK = 7
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc).date()


def _now():
    return datetime.now(timezone.utc)


async def run_weekly_digest(bot) -> None:
    logger.info("Weekly digest loop started (hourly check, 1/user/7d).")
    from utils.files import book_of_the_day
    while True:
        try:
            await asyncio.sleep(_INTERVAL)
            db = await MongoManager.get()
            now = _now()
            week_ago = now - timedelta(days=_WEEK)
            month_ago = now - timedelta(days=30)
            # global content — computed once per tick
            new_count = await db.count_global("files", {"indexed_at": {"$gte": week_ago}})
            botd = await book_of_the_day((now.date() - _EPOCH).days)
            botd_name = (botd or {}).get("name") if botd else None
            # opted-in, not banned, active in last 30d, not digested in last 7d
            targets = await db.find_global(
                "users",
                {"notif": {"$ne": False}, "is_banned": {"$ne": True},
                 "last_active": {"$gte": month_ago},
                 "last_digest": {"$not": {"$gte": week_ago}}},
                limit=_PER_TICK, proj={"user_id": 1, "login_streak": 1})
            sent = 0
            for u in targets:
                uid = u["user_id"]
                lines = ["📰 <b>Your Weekly Digest</b>", "━━━━━━━━━━━━━━━━━━"]
                if new_count:
                    lines.append(f"🆕 <b>{new_count}</b> new book(s) added this week")
                if botd_name:
                    lines.append(f"📖 Book of the Day: <b>{botd_name[:50]}</b>")
                lines.append(f"🔥 Your streak: <b>{int(u.get('login_streak') or 0)} day(s)</b> — "
                             "claim today to keep it going!")
                lines.append("\n🎁 Don't miss your free daily reward + spin.")
                try:
                    await bot.send_message(
                        uid, "\n".join(lines),
                        reply_markup=kb([btn("🎁 Daily Reward", "daily_reward", style="success"),
                                         btn("🔭 Discover", "lib_discover", style="primary")]))
                    sent += 1
                except Exception:  # noqa: BLE001 — blocked/deactivated
                    pass
                # stamp regardless so a blocking user isn't retried every tick
                await db.safe_update("users", {"user_id": uid}, {"$set": {"last_digest": now}})
                await asyncio.sleep(_SLEEP)
            if sent:
                logger.info("Weekly digests sent: %d", sent)
        except asyncio.CancelledError:
            logger.info("Weekly digest loop stopped.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Weekly digest loop error: %s", exc, exc_info=True)
