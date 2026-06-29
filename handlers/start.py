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

from config import ADMIN_IDS, REQUIRED_CHANNELS
from utils.brand import DASHBOARD_FOOTER, about_text
from utils.keyboards import btn, kb, url_btn
from utils.logs import log_new_user
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
        [btn("📚 Request a Book", "menu_request", style="primary"),
         btn("🎮 Play & Earn", "menu_games", style="success")],
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
    rows.append([btn("✅ I've Joined — Unlock My Library", "verify_join", style="success")])
    return kb(*rows)


# ── /start ───────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    # /start is the universal escape hatch: drop any half-finished flow (search,
    # gift, redeem, …) so the user is never trapped answering a stale prompt.
    await state.clear()
    if message.chat.type != "private":
        await message.answer(
            "👋 <b>Let's continue in private</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your library, wallet and rewards are personal to you, so I "
            "keep them in our private chat.\n\n"
            "📖 Tap my name and open a direct message — your reading hub is waiting "
            "there.</blockquote>",
        )
        return

    uid = message.chat.id
    if await is_banned(uid):
        await message.answer(
            "🔒 <b>Access Restricted</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This account is currently blocked from using the bot.\n\n"
            "If you believe this is a mistake, reach out to our team and we'll review "
            "it for you.</blockquote>")
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

    # first-ever /start → log to admin (full detail) + public (warm welcome).
    # is_new is now a true first-sight flag, so returning users are never re-logged.
    if doc.get("is_new"):
        await log_new_user(message.bot, uid, message.from_user.first_name or "",
                           message.from_user.username or "")

    await _render_gate_or_dashboard(message)


@router.callback_query(F.data == "verify_join")
async def cb_verify(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Verifying your channels… one moment ✨")
    await _render_gate_or_dashboard(call.message, override_user=call.from_user.id,
                                    first_name=call.from_user.first_name)


async def _render_gate_or_dashboard(message: Message, *, override_user: int = 0,
                                    first_name: str = "") -> None:
    uid = override_user or message.chat.id
    name = first_name or message.chat.first_name or "Reader"
    missing = await _not_joined(message.bot, uid)
    if missing:
        await message.answer(
            f"👋 <b>Welcome, {escape(name)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>One quick step and your library opens.</i>\n\n"
            "<blockquote>🔒 To keep the archive free and running, members join our "
            "official channels first — it takes just a moment.\n\n"
            "📢 Tap each channel below to join, then press <b>Verify</b> and we'll "
            "take it from here.</blockquote>",
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
            "👋 <b>Almost there</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Join our official channels below to unlock the archive, then "
            "tap your link once more — your title will be waiting.</blockquote>",
            reply_markup=_join_kb(missing))
        return
    f = await get_file(fuid)
    if not f:
        await message.answer(
            "🔭 <b>This title has moved on</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This file is no longer in our archive. It happens — try a "
            "fresh search and we'll help you find another copy or something even "
            "better.</blockquote>",
            reply_markup=_dashboard_kb())
        return
    await message.answer(
        f"📚 <b>{escape(f.get('name') or 'Your book')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Ready when you are.</i>\n\n"
        f"<blockquote>📄 <b>Format</b> · {icon_for(f.get('ext',''))} "
        f"<code>.{(f.get('ext') or '').upper()}</code>\n"
        f"💸 <b>Delivery</b> · just <code>1</code> 🪙 BCN or 💎 BGM\n\n"
        "Tap below and it's yours in an instant.</blockquote>",
        reply_markup=kb([btn("📥 Download Now", f"dl:{fuid}", style="success")],
                        [btn("🏠 Dashboard", "menu_home", style="primary")]))


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
        + f"👋 <b>{t('welcome', lang)}, {escape(name)}!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        + (f"{promo}\n\n" if promo else "")
        + f"✨ <i>{t('ready', lang)}</i>\n\n"
        "<blockquote>📚 <b>Request anything</b> — any eBook or audiobook, delivered "
        "straight to your chat\n"
        "📖 <b>Your private library</b> — read, listen, bookmark and pick up right "
        "where you left off\n"
        "🎮 <b>Play &amp; earn</b> — games, quests and daily rewards that top up your "
        "wallet\n"
        "💼 <b>One smart wallet</b> — 💎 BGM &amp; 🪙 BCN, redeems, gifts and VIP "
        "perks in one place</blockquote>\n\n"
        "<i>💡 Pick a tile below to begin — your shelf is ready when you are.</i>\n\n"
        + DASHBOARD_FOOTER,
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
        + f"👋 <b>{t('welcome', lang)}, {escape(call.from_user.first_name or 'Reader')}!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ <i>{t('ready', lang)}</i>\n\n"
        "<blockquote>You're back at your dashboard. Choose a tile below — request a "
        "title, open your library, play for rewards or manage your wallet.</blockquote>",
        reply_markup=await _dashboard_kb_with_ad(),
    )


@router.callback_query(F.data == "menu_library")
async def cb_library(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "📖 <b>My Library</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Everything you read, save and continue — gathered in one place.</i>\n\n"
        "<blockquote>🔭 <b>Discover</b> finds your next read, while <b>For You</b> and "
        "<b>AI Recommendations</b> learn your taste.\n"
        "📖 <b>Continue Reading</b> reopens your last page; ⭐ <b>Favorites</b> and "
        "📒 <b>My Shelf</b> keep it all organised.\n"
        "🎯 Set a <b>Reading Goal</b>, track your 📊 <b>stats</b>, and take on "
        "🎯 <b>Challenges</b> to keep the momentum going.</blockquote>",
        reply_markup=kb(
            [btn("🔭 Discover", "lib_discover", style="success"),
             btn("🎯 For You", "lib_foryou", style="success")],
            [btn("🤖 AI Recommendations", "lib_recommend", style="success")],
            [btn("📝 Book Summary", "lib_summary", style="success"),
             btn("📖 Continue Reading", "lib_continue", style="primary")],
            [btn("⭐ Favorites", "lib_favorites", style="primary"),
             btn("📒 My Shelf", "menu_shelf", style="primary")],
            [btn("📊 My Reading", "lib_stats", style="primary"),
             btn("🎯 Reading Goal", "lib_goal", style="primary")],
            [btn("🎯 Challenges", "menu_challenges", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ),
    )


@router.callback_query(F.data == "menu_account")
async def cb_account(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "👤 <b>My Account</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your profile, wallet and rewards — all under your control.</i>\n\n"
        "<blockquote>💼 Check your 💎 <b>BGM</b> &amp; 🪙 <b>BCN</b> balance, top up "
        "instantly, or claim your 🎁 <b>daily reward</b>.\n"
        "🎟 <b>Redeem</b> codes, 🎁 <b>gift</b> tokens to friends and earn more through "
        "<b>referrals</b>, 🚀 <b>quests</b> and 🎁 <b>loot crates</b>.\n"
        "👑 Unlock <b>VIP</b> perks, 🚨 track your requests, and tune your 🔔 alerts, "
        "🌐 language and 🆘 support — all in one place.</blockquote>\n\n"
        "<i>💡 New here? Claim your daily reward to start building your wallet.</i>",
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
        [btn("ℹ️ About Us", "menu_about", style="primary")],
    ]
    if call.from_user.id in ADMIN_IDS:
        rows.append([btn("🛠 Admin Centre", "admin_open", style="danger")])
    rows.append([btn("🔙 Back", "menu_home", style="danger")])
    await call.message.edit_text(
        "🛠️ <b>Bot Tools</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Stats, standings and everything behind the scenes.</i>\n\n"
        "<blockquote>🏆 Climb the <b>leaderboards</b>, watch the bot's live 📊 "
        "<b>stats</b>, and skim the public 📜 <b>activity log</b>.\n"
        "⭐ Love the service? <b>Rate us</b> in a tap, or read the ℹ️ <b>About</b> to "
        "meet the team behind your library.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data == "menu_about")
async def cb_about(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        about_text(),
        reply_markup=kb([btn("🔙 Back", "menu_tools", style="danger")]),
    )


# ── universal flow cancel ───────────────────────────────────────────────────────
# House rule: the bot never tells a user to send a command. Every prompt that used
# to say "send /cancel" now carries a ❌ Cancel button (utils.keyboards.cancel_btn)
# whose callback lands here. We clear any half-finished FSM flow and show a tidy
# card with a one-tap link back to wherever the user came from.
_CANCEL_DEST = {
    "menu_home": "🏠 Back to Menu",
    "menu_request": "📚 Back to Requests",
    "menu_library": "📖 Back to Library",
    "menu_account": "👤 Back to Account",
    "menu_tools": "🛠️ Back to Tools",
    "menu_shelf": "📒 Back to My Shelf",
    "menu_support": "🆘 Support",
    "acc_buy": "💎 Back to Top Up",
    "admin_open": "🛡 Back to Console",
    "admin_ai": "🤖 Back to AI Engine",
    "admin_manage": "🛡 Back to Manage Admins",
}


@router.callback_query(F.data.startswith("flow_cancel:"))
async def cb_flow_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Cancelled — nothing was saved.")
    target = (call.data.split(":", 1)[1] or "menu_home").strip() or "menu_home"
    label = _CANCEL_DEST.get(target, "🏠 Back to Menu")
    text = ("✖️ <b>Cancelled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No problem — that's been called off and nothing was saved.</i>\n\n"
            "<blockquote>Pick up wherever you like — your library, wallet and "
            "progress are exactly as you left them.</blockquote>")
    markup = kb([btn(label, target, style="primary")])
    # The prompt may have been a photo/caption message (edit_text would fail) or a
    # plain message — try to edit in place, otherwise send a fresh card.
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        await call.message.answer(text, reply_markup=markup)


# All dashboard actions now have real handlers in their own routers
# (recommend, requests_manual, track, economy, games, …). No "coming soon"
# stubs remain — adding one here would SHADOW the real handler, since start.router
# is included first.
