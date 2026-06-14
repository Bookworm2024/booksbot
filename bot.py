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
from aiohttp import web

from config import (
    BOT_TOKEN,
    PORT,
    TELEGRAM_API_BASE,
    validate_runtime_config,
)
from database.connection import MongoManager
from handlers import (
    admin, admin_tools, broadcast, captcha, daily, discover, economy, favorites,
    featured_admin, games, gift, indexer, inline, invite, payments, qadmin,
    missions, rate, recommend, referral, request, requests_manual, revenue,
    settings_admin, spin, start, stats, support, track, vip,
)
from handlers.payments import heleket_webhook
from handlers.admin_api import api_admin_overview
from handlers.bookle_api import api_bookle_new, api_bookle_guess
from handlers.games_api import api_game_new, api_game_submit
from handlers.reader_api import (
    api_file, api_reader_state_get, api_reader_state_set,
)
from middlewares.ban import BanMiddleware
from middlewares.maintenance import MaintenanceMiddleware
from utils.email_monitor import run_email_monitor
from utils.games import ensure_seed

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
    # Moderation + maintenance gates run before every handler.
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
    dp.include_router(discover.router)
    dp.include_router(games.router)
    dp.include_router(spin.router)
    dp.include_router(daily.router)
    dp.include_router(missions.router)
    dp.include_router(referral.router)
    dp.include_router(support.router)
    dp.include_router(rate.router)
    dp.include_router(stats.router)
    dp.include_router(inline.router)
    dp.include_router(invite.router)
    dp.include_router(broadcast.router)
    dp.include_router(qadmin.router)
    dp.include_router(revenue.router)
    dp.include_router(settings_admin.router)
    dp.include_router(featured_admin.router)
    dp.include_router(admin_tools.router)
    dp.include_router(admin.router)
    # indexer last — channel_post observer, no overlap with user handlers.
    dp.include_router(indexer.router)
    return dp


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
    # Reader / audiobook Mini-App API
    app.router.add_get("/api/file", api_file)
    app.router.add_get("/api/reader/state", api_reader_state_get)
    app.router.add_post("/api/reader/state", api_reader_state_set)
    # Heleket crypto payment webhook
    app.router.add_post("/heleket-webhook", heleket_webhook)
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
    await ensure_seed()  # seed the starter question bank if empty
    logger.info("MongoDB ready.")

    bot = _build_bot()
    dp = _build_dispatcher()
    runner = await _start_web(bot)

    # Background workers (UPI email auto-verify; no-op if IMAP unset).
    monitor_task = asyncio.create_task(run_email_monitor(bot))

    try:
        me = await bot.get_me()
        logger.info("Starting polling as @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down.")
