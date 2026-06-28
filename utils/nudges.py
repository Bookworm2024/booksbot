"""
utils/nudges.py — revenue nudges (abandoned-cart + low-balance upsell).

A background loop, separate from the win-back reminder loop, that gently
re-engages two monetisable segments:

  • Abandoned cart — opened 💎 Buy BGM but didn't pay within a couple of hours.
  • Low-balance upsell — engaged users (have downloaded) who are now out of tokens.

Both respect the per-user notif toggle, fire at most once per cart / once per day,
and are rate-limited well under Telegram's flood limits.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from database.connection import MongoManager

logger = logging.getLogger(__name__)

_INTERVAL = 1800          # check every 30 min
_PER_TICK = 100           # cap per segment per tick
_SLEEP = 0.05             # ~20 msgs/sec
_CART_MIN_AGE_H = 2       # nudge a cart this many hours after it was opened
_CART_MAX_AGE_H = 48      # …but not older than this (stale)

_CART_TEXT = ("🛒 <b>Your top-up is still waiting</b>\n"
              "<i>Right where you left it — ready whenever you are.</i>\n"
              "<blockquote>"
              "💎 <b>BookGems</b> are your permanent currency — buy once and they "
              "never expire.\n"
              "⚡ <b>Instant unlock</b> — finish checkout and your next read is one "
              "tap away.\n"
              "🎁 <b>Buy more, save more</b> — larger top-ups earn a bigger bonus on "
              "the house."
              "</blockquote>"
              "<i>💡 Pick up where you left off — we'll have your library ready in "
              "seconds.</i>")
_LOWBAL_TEXT = ("💼 <b>Your wallet's running low</b>\n"
                "<i>A quick refill and you're back to reading — no limits.</i>\n"
                "<blockquote>"
                "🎁 <b>Claim free 🪙 BCN</b> with /claim — a fresh daily reward, "
                "on us.\n"
                "🎡 <b>Take a free spin</b> for a shot at bonus tokens and perks.\n"
                "💎 <b>Top up 💎 BGM</b> for permanent credit that never expires."
                "</blockquote>"
                "<i>💡 Free tokens cover most reads — start with your daily claim "
                "below.</i>")


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


async def _buy_kb():
    from utils.keyboards import btn, kb
    return kb([btn("💎 Top Up BGM", "acc_buy", style="success"),
               btn("🎁 Claim Daily Reward", "daily_reward", style="primary")])


async def _abandoned_cart(bot, db) -> int:
    now = _now()
    lo, hi = now - timedelta(hours=_CART_MAX_AGE_H), now - timedelta(hours=_CART_MIN_AGE_H)
    targets = await db.find_global(
        "users",
        {"cart_opened_at": {"$gt": lo, "$lt": hi}, "cart_nudged": {"$ne": True},
         "notif": {"$ne": False}, "is_banned": {"$ne": True}},
        limit=_PER_TICK, proj={"user_id": 1})
    kbd = await _buy_kb()
    sent = 0
    for u in targets:
        uid = u["user_id"]
        try:
            await bot.send_message(uid, _CART_TEXT, reply_markup=kbd)
            sent += 1
        except Exception:  # noqa: BLE001
            pass
        await db.safe_update("users", {"user_id": uid},
                             {"$set": {"cart_nudged": True}}, upsert=False)
        await asyncio.sleep(_SLEEP)
    return sent


async def _low_balance(bot, db) -> int:
    from utils.wallet import get_balances
    from utils.settings import get_float
    active_cut = _now() - timedelta(days=3)
    today = _today()
    # cheap pre-filter on the stored bookgem field; confirm with the summed balance
    candidates = await db.find_global(
        "users",
        {"downloads": {"$gte": 1}, "last_active": {"$gte": active_cut},
         "lowbal_nudged": {"$ne": today}, "notif": {"$ne": False},
         "is_banned": {"$ne": True}, "bookgem": {"$lte": 0.05}},
        limit=_PER_TICK, proj={"user_id": 1})
    cost = await get_float("download_cost")
    kbd = await _buy_kb()
    sent = 0
    for u in candidates:
        uid = u["user_id"]
        bgm, bcn = await get_balances(uid)
        if bgm + bcn >= cost:
            continue  # actually has enough — skip
        try:
            await bot.send_message(uid, _LOWBAL_TEXT, reply_markup=kbd)
            sent += 1
        except Exception:  # noqa: BLE001
            pass
        await db.safe_update("users", {"user_id": uid},
                             {"$set": {"lowbal_nudged": today}}, upsert=False)
        await asyncio.sleep(_SLEEP)
    return sent


async def run_nudge_loop(bot) -> None:
    logger.info("Nudge loop started (abandoned-cart + low-balance, every %dm).",
                _INTERVAL // 60)
    while True:
        try:
            await asyncio.sleep(_INTERVAL)
            db = await MongoManager.get()
            c = await _abandoned_cart(bot, db)
            l = await _low_balance(bot, db)
            if c or l:
                logger.info("Nudges sent — cart: %d, low-balance: %d", c, l)
        except asyncio.CancelledError:
            logger.info("Nudge loop stopped.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Nudge loop error: %s", exc, exc_info=True)
