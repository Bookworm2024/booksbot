"""
utils/reminders.py — re-engagement push reminders (retention).

Background loop: once an hour, nudge users who have been INACTIVE for a while
(so we don't ping active users) to come back to their library — new arrivals and
the book they were reading. Respects the per-user notif toggle and sends at most
one reminder per user per day. Rate-limited so it never trips Telegram's flood
limits.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager

logger = logging.getLogger(__name__)

_INTERVAL = 3600          # check hourly
_INACTIVE_HOURS = 20      # only nudge users idle ≥ this long
_PER_TICK = 200           # cap users handled per tick (memory/flood safety)
_SLEEP = 0.05             # ~20 msgs/sec

_TEXT = ("📖 <b>Your library missed you</b>\n"
         "<i>Fresh titles landed while you were away.</i>\n"
         "<blockquote>"
         "🆕 <b>New arrivals</b> — brand-new books and audiobooks just hit the shelves.\n"
         "📚 <b>Continue reading</b> — your shelf is right where you left it.\n"
         "🎮 <b>Play &amp; earn</b> — a few quick games pay out 💎 BGM toward Premium."
         "</blockquote>"
         "<i>💡 Tap below to dive back in — your next great read is waiting.</i>")


def _kb():
    from utils.keyboards import btn, kb
    return kb([btn("🔭 Explore Discover", "lib_discover", style="success")],
              [btn("🏠 Open Menu", "menu_home", style="primary")])


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def run_reminder_loop(bot) -> None:
    logger.info("Reminder loop started (hourly, inactive≥%dh).", _INACTIVE_HOURS)
    while True:
        try:
            await asyncio.sleep(_INTERVAL)
            db = await MongoManager.get()
            cutoff = datetime.now(timezone.utc) - timedelta(hours=_INACTIVE_HOURS)
            today = _today()
            targets = await db.find_global(
                "users",
                {"notif": {"$ne": False}, "is_banned": {"$ne": True},
                 "last_active": {"$lt": cutoff}, "last_reminded": {"$ne": today}},
                limit=_PER_TICK, proj={"user_id": 1})
            sent = 0
            for u in targets:
                uid = u["user_id"]
                try:
                    await bot.send_message(uid, _TEXT, reply_markup=_kb())
                    sent += 1
                except Exception:  # noqa: BLE001 — blocked/deactivated
                    pass
                # stamp regardless so a blocking user isn't retried daily
                await db.safe_update("users", {"user_id": uid},
                                     {"$set": {"last_reminded": today}})
                await asyncio.sleep(_SLEEP)
            if sent:
                logger.info("Reminders sent: %d", sent)
        except asyncio.CancelledError:
            logger.info("Reminder loop stopped.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Reminder loop error: %s", exc, exc_info=True)
