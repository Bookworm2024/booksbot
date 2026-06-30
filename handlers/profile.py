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

from config import bot_username
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
    if vip: out.append(vip)
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
        f"<b>👤 {(flair + ' ') if flair else ''}{display} — Reader Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Your reading identity, level and lifetime story in one place.</i>\n\n"
        f"🏆 <b>Level {level}</b> · <i>{prog['title']}</i> · <code>{xp}</code> XP\n"
        f"{bar} <i>{into}/100 to your next level</i>\n\n"
        "<blockquote>"
        f"🏅 <b>Badges earned</b>\n{'  '.join(badges)}\n\n"
        f"📚 <b>Lifetime stats</b>\n"
        f"📥 Books unlocked · <code>{int(d.get('downloads') or 0)}</code>\n"
        f"🎮 Games played · <code>{int(d.get('games_played') or 0)}</code>    "
        f"🎁 Friends invited · <code>{int(d.get('ref_count') or 0)}</code>\n"
        f"⭐ Favourites saved · <code>{favs}</code>    "
        f"🎮 Game streak · <code>{int(d.get('game_streak') or 0)}d</code>"
        "</blockquote>\n"
        "<i>💡 Share your profile to invite friends — you both earn BGM when they join.</i>"
    )
    un = bot_username()
    share = (f"I'm Level {level} on @{un} 📚 — free books, audiobooks & "
             f"games. Join me!")
    share_url = f"https://t.me/share/url?url=https://t.me/{un}&text={quote(share)}"
    return text, kb([url_btn("📤 Share My Profile", share_url, style="success")],
                    [btn("📈 XP & Levels", "xp_view", style="primary"),
                     btn("🏅 Achievements", "acc_achievements", style="primary")],
                    [btn("🎨 Customise Profile", "acc_customize", style="primary")],
                    [btn("🔙 Back to Account", "menu_account", style="danger")])


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
        text, reply_markup=kb([btn("🔙 Back to Profile", "acc_profile", style="primary")]))


# ── XP & Levels ───────────────────────────────────────────────────────────────
async def _xp_view(uid: int) -> str:
    from utils.xp import get_progress, level_reward, title_for
    p = await get_progress(uid)
    return (
        "<b>📈 XP &amp; Levels</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Every action you take earns XP — and every level pays you back.</i>\n\n"
        f"🏆 <b>Level {p['level']}</b> · <i>{p['title']}</i>\n"
        f"{p['bar']}\n"
        f"⭐ <code>{p['xp']}</code> XP · <i>{p['remaining']} XP to reach Level "
        f"{p['level'] + 1}</i> ({title_for(p['level'] + 1)})\n\n"
        "<blockquote>"
        "<b>Ways to earn XP</b>\n"
        "📥 Unlock a book · <b>+5</b>\n"
        "🎮 Play a game · <b>+3</b>\n"
        "🎁 Claim your daily reward · <b>+2</b>\n"
        "🎡 Spin the wheel · <b>+1</b>\n"
        "⭐ Write a review · <b>+4</b>\n"
        "🎁 Refer a friend · <b>+20</b>"
        "</blockquote>\n"
        f"<i>💡 Every level-up pays a 💎 BGM bonus — your next is "
        f"<code>+{level_reward(p['level'] + 1):g} BGM</code>.</i>")


@router.callback_query(F.data == "xp_view")
async def cb_xp(call: CallbackQuery) -> None:
    await call.answer()
    text = await _xp_view(call.from_user.id)
    await call.message.edit_text(
        text, reply_markup=kb([btn("🏅 Achievements", "acc_achievements", style="primary"),
                               btn("🏆 Leaderboards", "lb_hub", style="primary")],
                              [btn("🔙 Back to Profile", "acc_profile", style="danger")]))


@router.message(Command("level"))
async def cmd_level(message: Message) -> None:
    text = await _xp_view(message.chat.id)
    await message.answer(text, reply_markup=kb(
        [btn("👤 View My Profile", "acc_profile", style="primary")]))
