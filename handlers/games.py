"""
handlers/games.py — Games hub (opens the Mini-App games).

🎮 Play Now → Quiz / True-False Mini Apps (server-scored) + how-to.
Falls back to a friendly notice if BOT_PUBLIC_URL isn't configured (Mini Apps
need an HTTPS host).
"""
import logging
from html import escape

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
            "🎮 <b>The Arcade is Resting</b>\n"
            "<i>A short intermission — we'll be back shortly.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>We're tuning the games to play their best. "
            "Your scores, streaks and rewards are all safe — pop back in a little while "
            "and the lights will be on.</blockquote>\n"
            "<i>💡 In the meantime, your library is always open.</i>",
            reply_markup=kb([btn("🔙 Back to Menu", "menu_home", style="danger")]))
        return
    if not BOT_PUBLIC_URL:
        await call.message.edit_text(
            "🎮 <b>The Arcade</b>\n"
            "<i>Almost ready for you.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>⚠️ Our Mini-App games need a secure address to call home "
            "(<code>BOT_PUBLIC_URL</code>). The moment we're fully deployed, the whole "
            "arcade lights up — quizzes, puzzles and rewards included.</blockquote>\n"
            "<i>💡 Check back soon — it's worth the wait.</i>",
            reply_markup=kb([btn("🔙 Back to Menu", "menu_home", style="danger")]))
        return
    await call.message.edit_text(
        "🎮 <b>The Arcade</b> — Play &amp; Earn 💎\n"
        "<i>Read, guess, race the clock — and bank real BGM as you go.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote expandable>🧠 <b>Brain Quiz</b> — choose your level; earn up to "
        "<b>0.125</b> 💎 BGM per correct answer, with a <b>+0.5</b> speed bonus for the swift.\n"
        "⚡ <b>True/False</b> — twenty rapid-fire calls; trust your instincts.\n"
        "📚 <b>Guess the Book</b> — name the title from its blurb alone.\n"
        "✍️ <b>First Line</b> — one opening sentence, one chance to place it.\n"
        "🖋️ <b>Author Match</b> — pair the masterpiece to its maker.\n"
        "🎭 <b>Cover Guess</b> — decode the book hidden inside the emoji.\n"
        "⚡ <b>Speed Read</b> — clock your words-per-minute, then prove your recall.\n"
        "🧠 <b>Memory Match</b> — watch the tiles, then echo the sequence.\n"
        "🟩 <b>Bookle</b> — the daily book-word puzzle; one fresh word each day.</blockquote>\n"
        "<blockquote>⏱ One generous <b>15-minute</b> clock per round · move between questions "
        "freely · <b>skipping is always free</b>.</blockquote>\n"
        "<i>💡 Fresh questions every time, scored securely on our side. Pick a game and let's play.</i>",
        reply_markup=kb(
            [webapp_btn("🧠 Brain Quiz", "game.html", query="game=quiz", style="success"),
             webapp_btn("⚡ True/False", "game.html", query="game=tf", style="success")],
            [webapp_btn("📚 Guess the Book", "game.html", query="game=guess", style="success")],
            [webapp_btn("✍️ First Line", "game.html", query="game=firstline", style="success"),
             webapp_btn("🖋️ Author Match", "game.html", query="game=author", style="success")],
            [webapp_btn("🟩 Bookle · Today's Word", "bookle.html", style="success"),
             btn("🎡 Daily Spin", "daily_spin", style="success")],
            [btn("🔤 Hangman", "menu_hangman", style="success"),
             btn("🔀 Anagram", "menu_anagram", style="success")],
            [btn("🎭 Cover Guess", "menu_coverguess", style="success"),
             btn("⚡ Speed Read", "menu_speedread", style="success")],
            [btn("🧠 Memory Match", "menu_memory", style="success")],
            [btn("🎯 Daily Missions", "menu_missions", style="primary"),
             btn("📈 XP & Levels", "xp_view", style="primary")],
            [btn("🎟️ Battle Pass", "menu_battlepass", style="success"),
             btn("🏆 Leaderboard", "game_leaderboard", style="primary")],
            [btn("🏆 Weekly Tournament", "game_tournament", style="success")],
            [btn("🔙 Back to Menu", "menu_home", style="danger")],
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
        body = ("<blockquote>The board is wide open — no champions crowned just yet. "
                "Play a round, earn your first 💎 BGM, and your name takes pole position.</blockquote>\n"
                "<i>💡 Be the first — the top spot is yours for the taking.</i>")
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        rows = "\n".join(
            f"{medals[i]} {escape((t.get('first_name') or 'Player')[:18])} — "
            f"<b><code>{t.get('game_bgm',0):.2f}</code> 💎 BGM</b> · "
            f"<i>{int(t.get('games_played',0))} games</i>"
            for i, t in enumerate(top))
        body = ("<blockquote expandable>" + rows + "</blockquote>\n"
                "<i>💡 Every win you bank lifts your standing — keep playing to climb.</i>")
    await call.message.edit_text(
        "🏆 <b>Games Leaderboard</b>\n"
        "<i>The all-time best of the Arcade, ranked by BGM earned.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n" + body,
        reply_markup=kb([btn("🏆 Weekly Tournament", "game_tournament", style="primary")],
                        [btn("🔙 Back to Arcade", "menu_games", style="danger")]))


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
        body = ("<blockquote>A brand-new week, a clean slate — no one has played yet. "
                "The first to step up sets the pace for everyone to chase.</blockquote>\n"
                "<i>💡 Play a round now and you'll lead the table.</i>")
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        rows = "\n".join(
            f"{medals[i]} {escape((t.get('first_name') or 'Player')[:18])} — "
            f"<b><code>{int(t.get('tour_games') or 0)}</code></b> games"
            for i, t in enumerate(top))
        body = "<blockquote expandable>" + rows + "</blockquote>"
    mine = ""
    if my:
        rank = await db.count_global("users",
                                     {"tour_week": wk, "tour_games": {"$gt": my}}) + 1
        mine = (f"\n<blockquote>👤 <b>Your run this week:</b> "
                f"<code>{my}</code> games played · currently ranked <b>#{rank}</b>.</blockquote>")
    await call.message.edit_text(
        f"🏆 <b>Weekly Tournament</b> · <code>{wk}</code>\n"
        "<i>Most games played this week takes the crown — every round counts.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n" + body + mine,
        reply_markup=kb([btn("🔄 Refresh Standings", "game_tournament", style="primary")],
                        [btn("🔙 Back to Arcade", "menu_games", style="danger")]))
