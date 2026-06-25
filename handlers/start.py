"""
handlers/start.py — onboarding + main dashboard.

Flow:
  /start
    → private-chat check
    → required-channel join gate (REQUIRED_CHANNELS)
    → main dashboard (the coloured 5-button menu)

Submenus mirror the original bot's grouping:
  My Library  (recommendations, favorites)
  My Account  (balance, buy, redeem, refer, track)
  Bot Tools   (stats, logs, admin centre)

Feature actions that belong to later phases (request flow, games, reader)
are wired to friendly "coming soon" stubs so navigation never dead-ends.
"""
import logging
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, LOG_CHANNEL_ID, REQUIRED_CHANNELS
from utils.keyboards import btn, kb, url_btn
from utils.referral import grant_referral, remember_referrer
from utils.users import ensure_user, is_banned

logger = logging.getLogger(__name__)
router = Router()


# ── helpers ──────────────────────────────────────────────────────────────────
async def _not_joined(bot, user_id: int) -> list[str]:
    """Return the subset of REQUIRED_CHANNELS the user has NOT joined."""
    missing: list[str] = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:  # noqa: BLE001 — bot not admin / private; skip, don't block
            continue
    return missing


def _dashboard_kb():
    return kb(
        [btn("📚 Request Now", "menu_request", style="primary"),
         btn("🎮 Play Now", "menu_games", style="success")],
        [btn("📖 My Library", "menu_library", style="primary"),
         btn("👤 My Account", "menu_account", style="success")],
        [btn("🛠️ Bot Tools", "menu_tools", style="danger")],
    )


async def _dashboard_kb_with_ad():
    """Dashboard keyboard plus a sponsored-ad button when an active ad slot
    exists (paid placement; impressions tracked). Never fails the dashboard."""
    markup = _dashboard_kb()
    try:
        from utils.ads import pick_active
        ad = await pick_active()
        if ad:
            markup.inline_keyboard.append(
                [btn(ad.get("label") or "📢 Sponsored", f"ad:{ad['ad_id']}", style="primary")])
    except Exception:  # noqa: BLE001
        pass
    return markup


def _join_kb(missing: list[str]):
    rows = []
    for i, ch in enumerate(missing, 1):
        rows.append([url_btn(f"📢 Join Channel {i}", f"https://t.me/{ch.lstrip('@')}")])
    rows.append([btn("✅ I've Joined — Verify", "verify_join", style="success")])
    return kb(*rows)


# ── /start ───────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    # /start is the universal escape hatch: drop any half-finished flow (search,
    # gift, redeem, …) so the user is never trapped answering a stale prompt.
    await state.clear()
    if message.chat.type != "private":
        await message.answer(
            "👋 <b>Hello!</b> Please talk to me in private to use my features.",
        )
        return

    uid = message.chat.id
    if await is_banned(uid):
        await message.answer("🚫 <b>Access Denied.</b> You are banned from this bot.")
        return

    doc = await ensure_user(uid, message.from_user.first_name or "Reader",
                            message.from_user.username or "")

    arg = (command.args or "").strip()
    # inline-mode deep link: ?start=dl_<fuid> → offer the file (token-gated)
    if arg.startswith("dl_"):
        await _deeplink_download(message, uid, arg[3:])
        return
    # referral attribution from the deep-link payload (?start=<referrer_id>)
    if arg:
        await remember_referrer(uid, arg)

    # new-user log
    if doc.get("is_new") and LOG_CHANNEL_ID:
        try:
            await message.bot.send_message(
                LOG_CHANNEL_ID,
                f"🆕 <b>New User</b>\n👤 {message.from_user.first_name}\n"
                f"🆔 <code>{uid}</code>",
            )
        except Exception:  # noqa: BLE001
            pass

    await _render_gate_or_dashboard(message)


@router.callback_query(F.data == "verify_join")
async def cb_verify(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Checking membership…")
    await _render_gate_or_dashboard(call.message, override_user=call.from_user.id,
                                    first_name=call.from_user.first_name)


async def _render_gate_or_dashboard(message: Message, *, override_user: int = 0,
                                    first_name: str = "") -> None:
    uid = override_user or message.chat.id
    name = first_name or message.chat.first_name or "Reader"
    missing = await _not_joined(message.bot, uid)
    if missing:
        await message.answer(
            f"👋 <b>Welcome, {name}!</b>\n\n"
            "To access the library, please join our official channels:",
            reply_markup=_join_kb(missing),
        )
        return
    # optional anti-bot gate (config CAPTCHA_ENABLED)
    from handlers.captcha import needs_verification, send_challenge
    if await needs_verification(uid):
        await send_challenge(message, uid)
        return
    await _send_dashboard(message, name)


async def _deeplink_download(message: Message, uid: int, fuid: str) -> None:
    """Land from an inline-search deep link → enforce join-gate, then offer the
    file via the normal token-gated download button."""
    from utils.files import get_file, icon_for
    missing = await _not_joined(message.bot, uid)
    if missing:
        await message.answer(
            "👋 Almost there — join our channels, then tap the link again:",
            reply_markup=_join_kb(missing))
        return
    f = await get_file(fuid)
    if not f:
        await message.answer("❌ That title is no longer available.",
                             reply_markup=_dashboard_kb())
        return
    await message.answer(
        f"📚 <b>{escape(f.get('name') or 'Your book')}</b>\n{icon_for(f.get('ext',''))} "
        f".{(f.get('ext') or '').upper()}\n\n💸 1 BCN/BGM to download.",
        reply_markup=kb([btn("📥 Get it now", f"dl:{fuid}", style="success")],
                        [btn("🏠 Menu", "menu_home", style="primary")]))


async def _send_dashboard(message: Message, name: str) -> None:
    # pay out referral the first time the user clears the join-gate
    await grant_referral(message.bot, message.chat.id)
    from utils.deals import banner
    from utils.pricing import hh_banner
    from utils.xp import levelup_banner
    deal = await banner()
    happy = await hh_banner()
    promo = "\n".join(b for b in (deal, happy) if b)
    lvlup = await levelup_banner(message.chat.id)
    from utils.i18n import get_lang, t
    lang = await get_lang(message.chat.id)
    await message.answer(
        lvlup
        + f"👋 <b>{t('welcome', lang)}, {name}!</b>\n\n"
        + (f"{promo}\n\n" if promo else "")
        + f"✨ <b>{t('ready', lang)}</b>\n\n"
        "<blockquote>📚 <b>Explore Features:</b>\n"
        "• Request any eBook or Audiobook\n"
        "• Manage tokens and your library\n"
        "• Play games and earn rewards\n"
        "• Use advanced utility tools</blockquote>",
        reply_markup=await _dashboard_kb_with_ad(),
    )


# ── dashboard navigation ───────────────────────────────────────────────────────
@router.callback_query(F.data == "menu_home")
async def cb_home(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()  # leaving to the dashboard exits any half-finished flow
    await call.answer()
    from utils.xp import levelup_banner
    from utils.i18n import get_lang, t
    lvlup = await levelup_banner(call.from_user.id)
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        lvlup
        + f"👋 <b>{t('welcome', lang)}, {call.from_user.first_name or 'Reader'}!</b>\n\n"
        f"✨ <b>{t('ready', lang)}</b>",
        reply_markup=await _dashboard_kb_with_ad(),
    )


@router.callback_query(F.data == "menu_library")
async def cb_library(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "<b>📖 My Library</b>\n\nYour personal reading hub.",
        reply_markup=kb(
            [btn("🔭 Discover", "lib_discover", style="success"),
             btn("🎯 For You", "lib_foryou", style="success")],
            [btn("🤖 AI Recommendations", "lib_recommend", style="success")],
            [btn("📝 Book Summary", "lib_summary", style="success"),
             btn("📖 Continue Reading", "lib_continue", style="primary")],
            [btn("⭐ Favorites", "lib_favorites", style="primary"),
             btn("📒 My Shelf", "menu_shelf", style="primary")],
            [btn("📊 My Reading", "lib_stats", style="primary"),
             btn("📌 Reading List", "lib_tbr", style="primary")],
            [btn("🎯 Reading Goal", "lib_goal", style="primary")],
            [btn("👥 Book Clubs", "menu_clubs", style="success"),
             btn("🎯 Challenges", "menu_challenges", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ),
    )


@router.callback_query(F.data == "menu_account")
async def cb_account(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "<b>👤 My Account</b>\n\nProfile, tokens, rewards and activity.",
        reply_markup=kb(
            [btn("👤 Profile", "acc_profile", style="primary"),
             btn("💼 Balance", "acc_balance", style="primary")],
            [btn("💎 Buy BGM", "acc_buy", style="success")],
            [btn("🎁 Daily Reward", "daily_reward", style="success"),
             btn("👑 Premium (VIP)", "acc_vip", style="success")],
            [btn("🎟 Redeem Code", "acc_redeem", style="success"),
             btn("🎁 Refer & Earn", "acc_refer", style="primary")],
            [btn("🎁 Loot Crates", "menu_crates", style="success"),
             btn("🚀 Quests", "menu_quests", style="success")],
            [btn("🚨 Track Request", "acc_track", style="primary"),
             btn("🎁 Gift BGM", "acc_gift", style="success")],
            [btn("🔔 Notifications", "acc_notifs", style="primary"),
             btn("🌐 Language", "menu_locale", style="primary")],
            [btn("🆘 Support", "menu_support", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ),
    )


@router.callback_query(F.data == "menu_tools")
async def cb_tools(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    rows = [
        [btn("🏆 Leaderboards", "lb_hub", style="primary"),
         btn("📊 Bot Stats", "tool_stats", style="primary")],
        [btn("⭐ Rate Us", "menu_rate", style="primary"),
         btn("📜 Public Logs", "tool_logs", style="primary")],
    ]
    if call.from_user.id in ADMIN_IDS:
        rows.append([btn("🛠 Admin Centre", "admin_open", style="danger")])
    rows.append([btn("🔙 Back", "menu_home", style="danger")])
    await call.message.edit_text("<b>🛠️ Bot Tools</b>\n\nUtilities and system info.",
                                 reply_markup=kb(*rows))


# All dashboard actions now have real handlers in their own routers
# (recommend, requests_manual, track, economy, games, …). No "coming soon"
# stubs remain — adding one here would SHADOW the real handler, since start.router
# is included first.
