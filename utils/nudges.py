"""
utils/nudges.py — revenue nudges (abandoned-cart + premium upsell).

A background loop, separate from the win-back reminder loop, that gently
re-engages two monetisable segments:

  • Abandoned cart — opened 💳 Wallet top-up but didn't pay within a couple hours.
  • Premium upsell — free users who have used up today's free download quota
    (the precise moment Premium is most appealing).

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
              "💳 Your <b>wallet</b> balance never expires — top up once and spend it on "
              "👑 Premium or extra downloads whenever you like.\n"
              "⚡ <b>Instant</b> — finish checkout and you're set in seconds."
              "</blockquote>"
              "<i>💡 Pick up where you left off — it only takes a moment.</i>")
_UPSELL_TEXT = ("👑 <b>You're reading a lot today — nice!</b>\n"
                "<i>You've used up today's free downloads.</i>\n"
                "<blockquote>"
                "📥 <b>Go Premium</b> for <b>unlimited</b> downloads, the full Discover, "
                "5 AI searches &amp; summaries a day, and more game plays.\n"
                "💎 Short on cash? Redeem the <b>BGM</b> you've earned in games &amp; "
                "referrals for a free Premium week."
                "</blockquote>"
                "<i>💡 Or grab just this one file with a small wallet charge — your call.</i>")


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _cart_kb():
    from utils.keyboards import btn, kb
    return kb([btn("💳 Finish Top Up", "acc_buy", style="success")],
              [btn("👑 Get Premium", "go_premium", style="primary")])


def _upsell_kb():
    from utils.keyboards import btn, kb
    return kb([btn("👑 Go Premium", "go_premium", style="success")],
              [btn("💎 Redeem BGM → Premium", "go_premium", style="primary")])


async def _abandoned_cart(bot, db) -> int:
    now = _now()
    lo, hi = now - timedelta(hours=_CART_MAX_AGE_H), now - timedelta(hours=_CART_MIN_AGE_H)
    targets = await db.find_global(
        "users",
        {"cart_opened_at": {"$gt": lo, "$lt": hi}, "cart_nudged": {"$ne": True},
         "notif": {"$ne": False}, "is_banned": {"$ne": True}},
        limit=_PER_TICK, proj={"user_id": 1})
    kbd = _cart_kb()
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


async def _premium_upsell(bot, db) -> int:
    """Nudge free users who hit today's free download quota toward Premium — once
    per day, never premium members."""
    from utils.premium import is_premium
    from utils.settings import get_float
    free_lim = int(await get_float("q_dl_free"))
    if free_lim < 0:
        return 0  # downloads unlimited for everyone → nothing to upsell
    today = _today()
    candidates = await db.find_global(
        "users",
        {"q_dl_d": today, "q_dl_n": {"$gte": free_lim},
         "premup_nudged": {"$ne": today}, "notif": {"$ne": False},
         "is_banned": {"$ne": True}},
        limit=_PER_TICK, proj={"user_id": 1})
    kbd = _upsell_kb()
    sent = 0
    for u in candidates:
        uid = u["user_id"]
        if await is_premium(uid):
            continue  # already Premium — nothing to sell
        try:
            await bot.send_message(uid, _UPSELL_TEXT, reply_markup=kbd)
            sent += 1
        except Exception:  # noqa: BLE001
            pass
        await db.safe_update("users", {"user_id": uid},
                             {"$set": {"premup_nudged": today}}, upsert=False)
        await asyncio.sleep(_SLEEP)
    return sent


async def run_nudge_loop(bot) -> None:
    logger.info("Nudge loop started (abandoned-cart + premium upsell, every %dm).",
                _INTERVAL // 60)
    while True:
        try:
            await asyncio.sleep(_INTERVAL)
            db = await MongoManager.get()
            c = await _abandoned_cart(bot, db)
            p = await _premium_upsell(bot, db)
            if c or p:
                logger.info("Nudges sent — cart: %d, premium: %d", c, p)
        except asyncio.CancelledError:
            logger.info("Nudge loop stopped.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Nudge loop error: %s", exc, exc_info=True)
