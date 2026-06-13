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
from handlers import admin, start
from middlewares.ban import BanMiddleware

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
    # Moderation gate runs before every handler.
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    dp.include_router(start.router)
    dp.include_router(admin.router)
    return dp


# ── aiohttp web server (health + Mini Apps) ─────────────────────────────────────
async def _health(_req: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "booksbot"})


async def _start_web() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)
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
    logger.info("MongoDB ready.")

    bot = _build_bot()
    dp = _build_dispatcher()
    runner = await _start_web()

    try:
        me = await bot.get_me()
        logger.info("Starting polling as @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down.")
