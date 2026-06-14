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
        "🧠 <b>Quiz</b> — pick a level, up to <b>0.125</b>/Q (+0.5 speed bonus)\n"
        "✅ <b>True/False</b> — 20 Qs, beat the 15-min clock\n"
        "📚 <b>Guess the Book</b> — name the book from a blurb\n"
        "✍️ <b>First Line</b> — name the book from its opening line\n"
        "🖋️ <b>Author Match</b> — who wrote it?\n\n"
        "<i>Scored securely on our servers — no cheating possible.</i>",
        reply_markup=kb(
            [webapp_btn("🧠 Quiz", "game.html", query="game=quiz", style="success"),
             webapp_btn("✅ True/False", "game.html", query="game=tf", style="success")],
            [webapp_btn("📚 Guess the Book", "game.html", query="game=guess", style="success")],
            [webapp_btn("✍️ First Line", "game.html", query="game=firstline", style="success"),
             webapp_btn("🖋️ Author Match", "game.html", query="game=author", style="success")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ))
