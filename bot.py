"""
bot.py — BooksBot entry point.

Runs three things in one process:
  1. aiogram long-polling (the Telegram bot)
  2. an aiohttp server: /health probe + /app/* Mini-App static hosting
  3. (later) background workers

Designed to run identically on Koyeb, Render, Railway, Fly or a VPS via the
Dockerfile. Reads $PORT and binds 0.0.0.0.
"""
import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from aiohttp import web

from config import (
    BOT_TOKEN,
    PORT,
    TELEGRAM_API_BASE,
    validate_runtime_config,
)
from database.connection import MongoManager
from handlers import (
    abtest, admin, admin_extra, admin_tools, ai_admin, anagram, broadcast, captcha,
    ads, battlepass, challenges, channel_admin, clubs, cosmetics, coverguess, crates, daily,
    dedupe_admin, discover, economy, fallback, favorites, featured_admin, feed, games, gift,
    goals, health_admin, indexer, locale, memory, moderation_admin, perms_admin,
    inline, invite, hangman, payments, qadmin, leaderboards, missions, notifs, profile,
    quests, rate, ratings, recommend, referral, report, request, requests_manual, revenue,
    settings_admin, shelf, speedread, spin, start, stats, support, tbr, tagger, track, vip,
    pricing_admin, risk_admin,
)
from handlers.payments import oxapay_webhook
from handlers.pay_api import api_pay_cancel, api_pay_ipaid, api_pay_status
from handlers.admin_api import api_admin_overview, api_admin_ai, api_admin_ai_test
from handlers.broadcast import run_scheduled_broadcasts
from handlers.bookle_api import api_bookle_new, api_bookle_guess
from handlers.games_api import api_game_new, api_game_submit
from handlers.reader_api import (
    api_file, api_reader_state_get, api_reader_state_set,
)
from middlewares.ban import BanMiddleware
from middlewares.maintenance import MaintenanceMiddleware
from middlewares.metrics import MetricsMiddleware
from middlewares.ratelimit import RateLimitMiddleware
from utils.admins import load_extra_admins
from utils.email_monitor import run_email_monitor
from utils.files import backfill_chan_id
from utils.games import ensure_seed
from utils.digest import run_weekly_digest
from utils.reminders import run_reminder_loop
from utils.users import backfill_first_purchase_flag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("booksbot")

WEB_APP_DIR = os.path.join(os.path.dirname(__file__), "web_app")


def _build_bot() -> Bot:
    default = DefaultBotProperties(parse_mode=ParseMode.HTML)
    if TELEGRAM_API_BASE:
        # Point at a custom (coloured-button-capable) Bot API server.
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer
        session = AiohttpSession(api=TelegramAPIServer.from_base(TELEGRAM_API_BASE))
        return Bot(BOT_TOKEN, session=session, default=default)
    return Bot(BOT_TOKEN, default=default)


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Gates run before every handler, in order: metrics → flood limiter → ban →
    # maintenance. Metrics is outermost so it counts every update (even dropped
    # floods); rate limiting is next so floods are dropped before any DB work.
    dp.message.middleware(MetricsMiddleware())
    dp.callback_query.middleware(MetricsMiddleware())
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())
    # start first (owns the dashboard + nav), then feature routers.
    dp.include_router(start.router)
    dp.include_router(captcha.router)
    dp.include_router(request.router)
    dp.include_router(requests_manual.router)
    dp.include_router(track.router)
    dp.include_router(economy.router)
    dp.include_router(payments.router)
    dp.include_router(gift.router)
    dp.include_router(vip.router)
    dp.include_router(recommend.router)
    dp.include_router(favorites.router)
    dp.include_router(shelf.router)
    dp.include_router(tbr.router)
    dp.include_router(goals.router)
    dp.include_router(feed.router)
    dp.include_router(discover.router)
    dp.include_router(challenges.router)
    dp.include_router(clubs.router)
    dp.include_router(games.router)
    dp.include_router(hangman.router)
    dp.include_router(anagram.router)
    dp.include_router(coverguess.router)
    dp.include_router(speedread.router)
    dp.include_router(memory.router)
    dp.include_router(spin.router)
    dp.include_router(daily.router)
    dp.include_router(crates.router)
    dp.include_router(quests.router)
    dp.include_router(battlepass.router)
    dp.include_router(missions.router)
    dp.include_router(profile.router)
    dp.include_router(cosmetics.router)
    dp.include_router(leaderboards.router)
    dp.include_router(referral.router)
    dp.include_router(support.router)
    dp.include_router(report.router)
    dp.include_router(notifs.router)
    dp.include_router(locale.router)
    dp.include_router(rate.router)
    dp.include_router(ratings.router)
    dp.include_router(stats.router)
    dp.include_router(inline.router)
    dp.include_router(invite.router)
    dp.include_router(broadcast.router)
    dp.include_router(abtest.router)
    dp.include_router(qadmin.router)
    dp.include_router(revenue.router)
    dp.include_router(settings_admin.router)
    dp.include_router(pricing_admin.router)
    dp.include_router(ads.router)
    dp.include_router(channel_admin.router)
    dp.include_router(featured_admin.router)
    dp.include_router(tagger.router)
    dp.include_router(ai_admin.router)
    dp.include_router(admin_extra.router)
    dp.include_router(admin_tools.router)
    dp.include_router(health_admin.router)
    dp.include_router(moderation_admin.router)
    dp.include_router(perms_admin.router)
    dp.include_router(dedupe_admin.router)
    dp.include_router(risk_admin.router)
    dp.include_router(admin.router)
    _register_error_handler(dp)
    # fallback last among message routers: catches stray non-command text only
    # when no FSM flow is active, so it can never shadow a real handler.
    dp.include_router(fallback.router)
    # indexer last — channel_post observer, no overlap with user handlers.
    dp.include_router(indexer.router)
    return dp


def _register_error_handler(dp: Dispatcher) -> None:
    """Capture any unhandled handler exception (utils.errors) instead of letting
    it surface as a bare traceback — feeds the admin 🩺 Health error feed."""
    @dp.errors()
    async def _on_error(event: ErrorEvent) -> bool:
        from utils.errors import capture
        update = getattr(event, "update", None)
        where = str(getattr(update, "event_type", "update")) if update else "update"
        await capture(event.exception, where=where)
        logger.exception("Unhandled handler error: %s", event.exception)
        return True  # mark handled so polling continues


# ── aiohttp web server (health + Mini Apps) ─────────────────────────────────────
async def _health(_req: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "booksbot"})


async def _start_web(bot: Bot) -> web.AppRunner:
    app = web.Application(client_max_size=30 * 1024 * 1024)
    app["bot"] = bot  # reader_api streams files via the bot
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)
    # Mini-App game API
    app.router.add_post("/api/game/new", api_game_new)
    app.router.add_post("/api/game/submit", api_game_submit)
    app.router.add_post("/api/bookle/new", api_bookle_new)
    app.router.add_post("/api/bookle/guess", api_bookle_guess)
    app.router.add_get("/api/admin/overview", api_admin_overview)
    app.router.add_get("/api/admin/ai", api_admin_ai)
    app.router.add_post("/api/admin/ai", api_admin_ai)
    app.router.add_post("/api/admin/ai/test", api_admin_ai_test)
    # Reader / audiobook Mini-App API
    app.router.add_get("/api/file", api_file)
    app.router.add_get("/api/reader/state", api_reader_state_get)
    app.router.add_post("/api/reader/state", api_reader_state_set)
    # OxaPay crypto payment webhook
    app.router.add_post("/oxapay-webhook", oxapay_webhook)
    # Payment Mini-App (web_app/pay.html) — UPI UTR submit + live status + cancel
    app.router.add_get("/api/pay/status", api_pay_status)
    app.router.add_post("/api/pay/ipaid", api_pay_ipaid)
    app.router.add_post("/api/pay/cancel", api_pay_cancel)
    if os.path.isdir(WEB_APP_DIR):
        app.router.add_static("/app/", WEB_APP_DIR, show_index=False)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Web server listening on 0.0.0.0:%d", PORT)
    return runner


async def main() -> None:
    problems = validate_runtime_config()
    if problems:
        logger.error("Configuration problems:\n  - %s", "\n  - ".join(problems))
        sys.exit(1)

    # Connect Mongo up front so a bad URL fails fast & loud.
    await MongoManager.get()
    await ensure_seed()                  # seed the starter question bank if empty
    await load_extra_admins()            # merge admins added via /admin into ADMIN_IDS
    await backfill_first_purchase_flag()  # protect first-purchase bonus from existing buyers
    await backfill_chan_id()             # stamp legacy files with their source channel
    logger.info("MongoDB ready.")

    from utils.metrics import mark_start
    from utils.backup import run_backup_loop
    from utils.nudges import run_nudge_loop
    mark_start()

    bot = _build_bot()
    dp = _build_dispatcher()
    runner = await _start_web(bot)

    # Background workers (UPI email auto-verify; comeback reminders; scheduled
    # broadcasts; weekly digest; automated config/economy backups).
    monitor_task = asyncio.create_task(run_email_monitor(bot))
    reminder_task = asyncio.create_task(run_reminder_loop(bot))
    sched_bc_task = asyncio.create_task(run_scheduled_broadcasts(bot))
    digest_task = asyncio.create_task(run_weekly_digest(bot))
    backup_task = asyncio.create_task(run_backup_loop(bot))
    nudge_task = asyncio.create_task(run_nudge_loop(bot))

    try:
        me = await bot.get_me()
        logger.info("Starting polling as @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        reminder_task.cancel()
        sched_bc_task.cancel()
        digest_task.cancel()
        backup_task.cancel()
        nudge_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down.")
