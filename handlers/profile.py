"""
handlers/profile.py — player profile (level, XP, badges).

Account → 👤 Profile → a gamified card: level + XP progress, earned badges,
and lifetime stats. Shareable to pull friends in.
"""
import logging
from html import escape
from urllib.parse import quote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import BOT_USERNAME
from database.connection import MongoManager
from utils.keyboards import btn, kb, url_btn
from utils.vip import badge as vip_badge

logger = logging.getLogger(__name__)
router = Router()


def _badges(d: dict, favs: int, vip: str) -> list[str]:
    out = []
    dl = int(d.get("downloads") or 0)
    if dl >= 100: out.append("📚 Master Reader")
    elif dl >= 50: out.append("📚 Avid Reader")
    elif dl >= 10: out.append("📖 Bookworm")
    gp = int(d.get("games_played") or 0)
    if gp >= 50: out.append("🎮 Game Master")
    elif gp >= 10: out.append("🎮 Gamer")
    rc = int(d.get("ref_count") or 0)
    if rc >= 25: out.append("🌟 Influencer")
    elif rc >= 5: out.append("🤝 Connector")
    ls = int(d.get("login_streak") or 0)
    if ls >= 30: out.append("🔥 Devoted")
    elif ls >= 7: out.append("🔥 Regular")
    if favs >= 10: out.append("⭐ Curator")
    if vip: out.append("👑 " + vip)
    return out or ["🌱 Newcomer"]


async def _view(uid: int, name: str):
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": uid}) or {}
    favs = await db.count_global("favorites", {"user_id": uid})
    vip = await vip_badge(uid)
    from utils.xp import get_progress
    prog = await get_progress(uid)
    xp, level, into, bar = prog["xp"], prog["level"], prog["into"], prog["bar"]
    badges = _badges(d, favs, vip)
    flair = d.get("equipped_flair") or ""
    display = escape(d.get("vanity") or name)   # name is the raw Telegram first_name
    text = (
        f"<b>👤 {(flair + ' ') if flair else ''}{display}'s Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Level {level}</b> · {prog['title']} · {xp} XP\n"
        f"{bar} <i>{into}/100 to next</i>\n\n"
        f"<b>Badges</b>\n{'  '.join(badges)}\n\n"
        f"📥 Downloads: <b>{int(d.get('downloads') or 0)}</b>\n"
        f"🎮 Games: <b>{int(d.get('games_played') or 0)}</b> · "
        f"🎁 Referrals: <b>{int(d.get('ref_count') or 0)}</b>\n"
        f"⭐ Favorites: <b>{favs}</b> · 🔥 Login: <b>{int(d.get('login_streak') or 0)}d</b> · "
        f"🎮 Game streak: <b>{int(d.get('game_streak') or 0)}d</b>"
    )
    share = (f"I'm Level {level} on @{BOT_USERNAME} 📚 — free books, audiobooks & "
             f"games. Join me!")
    share_url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}&text={quote(share)}"
    return text, kb([url_btn("📤 Share Profile", share_url, style="success")],
                    [btn("📈 XP & Levels", "xp_view", style="primary"),
                     btn("🏅 Achievements", "acc_achievements", style="primary")],
                    [btn("🎨 Customize", "acc_customize", style="primary")],
                    [btn("🔙 Back", "menu_account", style="danger")])


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    text, markup = await _view(message.chat.id, message.from_user.first_name or "Reader")
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "acc_profile")
async def cb_profile(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id, call.from_user.first_name or "Reader")
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "acc_achievements")
async def cb_achievements(call: CallbackQuery) -> None:
    await call.answer()
    from utils.achievements import board
    text = await board(call.from_user.id)
    await call.message.edit_text(
        text, reply_markup=kb([btn("🔙 Back", "acc_profile", style="primary")]))


# ── XP & Levels ───────────────────────────────────────────────────────────────
async def _xp_view(uid: int) -> str:
    from utils.xp import get_progress, level_reward, title_for
    p = await get_progress(uid)
    return (
        "<b>📈 XP &amp; Levels</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Level {p['level']}</b> — {p['title']}\n"
        f"{p['bar']}\n"
        f"⭐ <b>{p['xp']} XP</b> · <i>{p['remaining']} XP to Level {p['level'] + 1}</i> "
        f"({title_for(p['level'] + 1)})\n\n"
        "<b>Earn XP by:</b>\n"
        "📥 Downloading a book — <b>+5</b>\n"
        "🎮 Playing a game — <b>+3</b>\n"
        "🪙 Claiming daily BCN — <b>+2</b>\n"
        "🎡 Spinning the wheel — <b>+1</b>\n"
        "⭐ Writing a review — <b>+4</b>\n"
        "🎁 Referring a friend — <b>+20</b>\n\n"
        f"<i>Every level-up pays a BGM bonus (next: +{level_reward(p['level'] + 1):g} BGM).</i>")


@router.callback_query(F.data == "xp_view")
async def cb_xp(call: CallbackQuery) -> None:
    await call.answer()
    text = await _xp_view(call.from_user.id)
    await call.message.edit_text(
        text, reply_markup=kb([btn("🏅 Achievements", "acc_achievements", style="primary"),
                               btn("🏆 Leaderboards", "lb_hub", style="primary")],
                              [btn("🔙 Back", "acc_profile", style="danger")]))


@router.message(Command("level"))
async def cmd_level(message: Message) -> None:
    text = await _xp_view(message.chat.id)
    await message.answer(text, reply_markup=kb(
        [btn("👤 Profile", "acc_profile", style="primary")]))
