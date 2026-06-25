"""
handlers/games.py — Games hub (opens the Mini-App games).

🎮 Play Now → Quiz / True-False Mini Apps (server-scored) + how-to.
Falls back to a friendly notice if BOT_PUBLIC_URL isn't configured (Mini Apps
need an HTTPS host).
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import BOT_PUBLIC_URL
from utils.keyboards import btn, kb, webapp_btn

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "menu_games")
async def cb_games(call: CallbackQuery, state: FSMContext) -> None:
    # leaving to the Games hub exits any half-finished game flow (e.g. a Cover
    # Guess round), so stray text later isn't captured as a guess.
    await state.clear()
    await call.answer()
    from utils.flags import is_on
    if not await is_on("games"):
        await call.message.edit_text(
            "🎮 <b>Games are paused</b> right now — check back soon!",
            reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    if not BOT_PUBLIC_URL:
        await call.message.edit_text(
            "🎮 <b>Games</b>\n\n⚠️ Mini-App games need the bot's public URL set "
            "(BOT_PUBLIC_URL). They'll light up once deployed.",
            reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    await call.message.edit_text(
        "<b>🎮 Arcade — Play &amp; Earn</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>Brain Quiz</b> — pick a level, up to <b>0.125</b> BGM/correct (+0.5 speed bonus)\n"
        "⚡ <b>True/False</b> — 20 rapid-fire calls\n"
        "📚 <b>Guess the Book</b> — name it from the blurb\n"
        "✍️ <b>First Line</b> — name it from line one\n"
        "🖋️ <b>Author Match</b> — who wrote it?\n"
        "🎭 <b>Cover Guess</b> — name the book from emoji\n"
        "⚡ <b>Speed Read</b> — measure your WPM + recall\n"
        "🟩 <b>Bookle</b> — the daily book-word puzzle\n\n"
        "⏱ One <b>15-minute</b> clock per round · jump between questions freely · "
        "<b>skipping is free</b>.\n"
        "<i>Fresh questions every time — scored securely server-side.</i>",
        reply_markup=kb(
            [webapp_btn("🧠 Brain Quiz", "game.html", query="game=quiz", style="success"),
             webapp_btn("⚡ True/False", "game.html", query="game=tf", style="success")],
            [webapp_btn("📚 Guess the Book", "game.html", query="game=guess", style="success")],
            [webapp_btn("✍️ First Line", "game.html", query="game=firstline", style="success"),
             webapp_btn("🖋️ Author Match", "game.html", query="game=author", style="success")],
            [webapp_btn("🟩 Bookle (daily)", "bookle.html", style="success"),
             btn("🎡 Daily Spin", "daily_spin", style="success")],
            [btn("🔤 Hangman", "menu_hangman", style="success"),
             btn("🔀 Anagram", "menu_anagram", style="success")],
            [btn("🎭 Cover Guess", "menu_coverguess", style="success"),
             btn("⚡ Speed Read", "menu_speedread", style="success")],
            [btn("🎯 Daily Missions", "menu_missions", style="primary"),
             btn("📈 XP & Levels", "xp_view", style="primary")],
            [btn("🎟️ Battle Pass", "menu_battlepass", style="success"),
             btn("🏆 Leaderboard", "game_leaderboard", style="primary")],
            [btn("🏆 Weekly Tournament", "game_tournament", style="success")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ))


@router.callback_query(F.data == "game_leaderboard")
async def cb_leaderboard(call: CallbackQuery) -> None:
    from database.connection import MongoManager
    await call.answer()
    db = await MongoManager.get()
    top = await db.find_global("users", {"game_bgm": {"$gt": 0}},
                               sort=[("game_bgm", -1)], limit=10,
                               proj={"first_name": 1, "game_bgm": 1, "games_played": 1})
    if not top:
        body = "No games played yet — be the first! 🎮"
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        body = "\n".join(
            f"{medals[i]} {(t.get('first_name') or 'Player')[:18]} — "
            f"<b>{t.get('game_bgm',0):.2f} BGM</b> ({int(t.get('games_played',0))} games)"
            for i, t in enumerate(top))
    await call.message.edit_text(
        "<b>🏆 Games Leaderboard</b>\n━━━━━━━━━━━━━━━━━━\n" + body,
        reply_markup=kb([btn("🏆 Weekly Tournament", "game_tournament", style="primary")],
                        [btn("🔙 Back", "menu_games", style="danger")]))


@router.callback_query(F.data == "game_tournament")
async def cb_tournament(call: CallbackQuery) -> None:
    from datetime import datetime, timezone
    from database.connection import MongoManager
    await call.answer()
    db = await MongoManager.get()
    wk = datetime.now(timezone.utc).strftime("%G-W%V")
    top = await db.find_global("users", {"tour_week": wk, "tour_games": {"$gt": 0}},
                               sort=[("tour_games", -1)], limit=10,
                               proj={"first_name": 1, "tour_games": 1})
    me = await db.find_one_global("users", {"user_id": call.from_user.id},
                                  {"tour_week": 1, "tour_games": 1}) or {}
    my = int(me.get("tour_games") or 0) if me.get("tour_week") == wk else 0
    if not top:
        body = "No games played this week yet — be the first! 🎮"
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        body = "\n".join(
            f"{medals[i]} {(t.get('first_name') or 'Player')[:18]} — "
            f"<b>{int(t.get('tour_games') or 0)}</b> games"
            for i, t in enumerate(top))
    mine = ""
    if my:
        rank = await db.count_global("users",
                                     {"tour_week": wk, "tour_games": {"$gt": my}}) + 1
        mine = f"\n\n👤 You: <b>{my}</b> games this week · rank <b>#{rank}</b>"
    await call.message.edit_text(
        f"🏆 <b>Weekly Tournament</b> · {wk}\n<i>Most games played this week wins!</i>\n"
        "━━━━━━━━━━━━━━━━━━\n" + body + mine,
        reply_markup=kb([btn("🔄 Refresh", "game_tournament", style="primary")],
                        [btn("🔙 Games", "menu_games", style="danger")]))
