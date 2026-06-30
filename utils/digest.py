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
from html import escape

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
                limit=_PER_TICK, proj={"user_id": 1})
            sent = 0
            for u in targets:
                uid = u["user_id"]
                lines = ["📰 <b>Your Weekly Digest</b>",
                         "<i>A week of reading, curated for you.</i>",
                         "━━━━━━━━━━━━━━━━━━━━",
                         "<blockquote>Here's what's worth your attention this "
                         "week — gathered from across the library and tailored "
                         "to keep your shelf moving.</blockquote>"]
                if new_count:
                    lines.append(f"🆕 <b>{new_count}</b> fresh title(s) arrived "
                                 "this week — first in line is yours.")
                if botd_name:
                    lines.append(f"📖 Today's <b>Book of the Day</b>: "
                                 f"<i>{escape(botd_name[:50])}</i>")
                lines.append("\n📚 <b>Pick up where you left off</b> — your shelf is "
                             "right where you left it, and new arrivals are waiting.")
                lines.append("<i>💡 Open Discover for curated shelves, popular "
                             "picks and what readers are loving now.</i>")
                try:
                    await bot.send_message(
                        uid, "\n".join(lines),
                        reply_markup=kb([btn("🔭 Explore Discover", "lib_discover", style="success")]))
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
