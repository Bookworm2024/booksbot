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

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
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


def _join_kb(missing: list[str]):
    rows = []
    for i, ch in enumerate(missing, 1):
        rows.append([url_btn(f"📢 Join Channel {i}", f"https://t.me/{ch.lstrip('@')}")])
    rows.append([btn("✅ I've Joined — Verify", "verify_join", style="success")])
    return kb(*rows)


# ── /start ───────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
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

    # referral attribution from the deep-link payload (?start=<referrer_id>)
    if command.args:
        await remember_referrer(uid, command.args.strip())

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
async def cb_verify(call: CallbackQuery) -> None:
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


async def _send_dashboard(message: Message, name: str) -> None:
    # pay out referral the first time the user clears the join-gate
    await grant_referral(message.bot, message.chat.id)
    await message.answer(
        f"👋 <b>Welcome back, {name}!</b>\n\n"
        "✨ <b>Your reading companion is ready.</b>\n\n"
        "<blockquote>📚 <b>Explore Features:</b>\n"
        "• Request any eBook or Audiobook\n"
        "• Manage tokens and your library\n"
        "• Play games and earn rewards\n"
        "• Use advanced utility tools</blockquote>",
        reply_markup=_dashboard_kb(),
    )


# ── dashboard navigation ───────────────────────────────────────────────────────
@router.callback_query(F.data == "menu_home")
async def cb_home(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        f"👋 <b>Welcome back, {call.from_user.first_name or 'Reader'}!</b>\n\n"
        "✨ <b>Your reading companion is ready.</b>",
        reply_markup=_dashboard_kb(),
    )


@router.callback_query(F.data == "menu_library")
async def cb_library(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        "<b>📖 My Library</b>\n\nYour personal reading hub.",
        reply_markup=kb(
            [btn("🤖 AI Recommendations", "lib_recommend", style="success"),
             btn("📝 Book Summary", "lib_summary", style="success")],
            [btn("📖 Continue Reading", "lib_continue", style="primary"),
             btn("⭐ Favorites", "lib_favorites", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ),
    )


@router.callback_query(F.data == "menu_account")
async def cb_account(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        "<b>👤 My Account</b>\n\nProfile, tokens, rewards and activity.",
        reply_markup=kb(
            [btn("💼 Balance", "acc_balance", style="primary"),
             btn("💎 Buy BGM", "acc_buy", style="success")],
            [btn("🎟 Redeem Code", "acc_redeem", style="success"),
             btn("🎁 Refer & Earn", "acc_refer", style="primary")],
            [btn("🚨 Track Request", "acc_track", style="primary"),
             btn("🆘 Support", "menu_support", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ),
    )


@router.callback_query(F.data == "menu_tools")
async def cb_tools(call: CallbackQuery) -> None:
    await call.answer()
    rows = [
        [btn("📊 Bot Stats", "tool_stats", style="primary"),
         btn("⭐ Rate Us", "menu_rate", style="primary")],
        [btn("📜 Public Logs", "tool_logs", style="primary")],
    ]
    if call.from_user.id in ADMIN_IDS:
        rows.append([btn("🛠 Admin Centre", "admin_open", style="danger")])
    rows.append([btn("🔙 Back", "menu_home", style="danger")])
    await call.message.edit_text("<b>🛠️ Bot Tools</b>\n\nUtilities and system info.",
                                 reply_markup=kb(*rows))


# ── phase-2 stubs (never dead-end) ─────────────────────────────────────────────
# Actions still pending a phase. Implemented actions (menu_request, acc_balance,
# acc_redeem, lib_favorites, …) are handled by their own routers and removed here.
_COMING = {
    "lib_recommend": "🤖 AI recommendations are coming soon (pending the LLM key).",
}


@router.callback_query(F.data.in_(set(_COMING)))
async def cb_coming(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        f"{_COMING[call.data]}",
        reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]),
    )
