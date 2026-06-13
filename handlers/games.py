"""
handlers/games.py — Games hub (opens the Mini-App games).

🎮 Play Now → Quiz / True-False Mini Apps (server-scored) + how-to.
Falls back to a friendly notice if BOT_PUBLIC_URL isn't configured (Mini Apps
need an HTTPS host).
"""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import BOT_PUBLIC_URL
from utils.keyboards import btn, kb, webapp_btn

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "menu_games")
async def cb_games(call: CallbackQuery) -> None:
    await call.answer()
    if not BOT_PUBLIC_URL:
        await call.message.edit_text(
            "🎮 <b>Games</b>\n\n⚠️ Mini-App games need the bot's public URL set "
            "(BOT_PUBLIC_URL). They'll light up once deployed.",
            reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    await call.message.edit_text(
        "<b>🎮 Play &amp; Earn</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>Quiz</b> — 8 Qs, pick a level, win up to <b>0.125 BGM</b>/Q "
        "(+0.5 speed bonus). 2/day.\n"
        "✅ <b>True/False</b> — 20 Qs, +0.05/Q, beat the 15-min clock. 2/day.\n\n"
        "<i>Scored securely on our servers — no cheating possible.</i>",
        reply_markup=kb(
            [webapp_btn("🧠 Play Quiz", "game.html", query="game=quiz", style="success")],
            [webapp_btn("✅ Play True/False", "game.html", query="game=tf", style="success")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ))
