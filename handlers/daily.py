"""
handlers/daily.py — daily login-streak reward (retention).

Claim once/day; the reward grows with your consecutive-day streak (resets if you
miss a day), capping at day 7. Atomic claim so it can't be double-collected.
"""
import calendar
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.format import fmt_amount, sanitize_amount
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

# reward by streak day 1..7 (BGM); streak ≥7 keeps the day-7 reward
_REWARDS = [0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.5]


def _d(offset: int = 0) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).strftime("%Y-%m-%d")


@router.message(Command("daily"))
async def cmd_daily(message: Message) -> None:
    await _claim(message, message.chat.id)


@router.callback_query(F.data == "daily_reward")
async def cb_daily(call: CallbackQuery) -> None:
    await call.answer()
    await _claim(call.message, call.from_user.id)


def _gap_days(prev_daily: str) -> int:
    """Whole days since the previous claim date (0 if unknown)."""
    if not prev_daily:
        return 0
    try:
        prev = datetime.strptime(prev_daily, "%Y-%m-%d").date()
        return (datetime.now(timezone.utc).date() - prev).days
    except (ValueError, TypeError):
        return 0


async def _claim(message: Message, uid: int) -> None:
    db = await MongoManager.get()
    today, yesterday, day_before = _d(0), _d(-1), _d(-2)
    # atomic: only the first claim today flips last_daily
    before = await db.find_one_and_update_global(
        "users", {"user_id": uid, "last_daily": {"$ne": today}},
        {"$set": {"last_daily": today}}, return_before=True)
    if before is None:
        doc = await db.find_one_global("users", {"user_id": uid},
                                       {"login_streak": 1, "streak_freezes": 1}) or {}
        frz = int(doc.get("streak_freezes") or 0)
        await message.answer(
            "🎁 <b>Today's Reward — Already Claimed</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>You're all set for today. Your streak is safe and "
            "counting — return tomorrow to claim the next, larger reward.\n"
            f"🔥 <b>Current streak:</b> <code>{int(doc.get('login_streak') or 0)}</code> day(s)"
            + (f"\n🛡 <b>Streak insurance:</b> <code>{frz}</code> freeze(s) banked" if frz else "")
            + "</blockquote>\n"
            "<i>💡 Rewards climb every day and peak on day 7 — keep the streak alive.</i>",
            reply_markup=kb([btn("💼 My Wallet", "acc_balance", style="primary")]))
        return

    prev_streak = int(before.get("login_streak") or 0)
    freezes = int(before.get("streak_freezes") or 0)
    prev_daily = before.get("last_daily")

    # ── streak + insurance ──────────────────────────────────────────────────
    insured = False
    if prev_daily == yesterday:
        streak = prev_streak + 1
    elif prev_daily == day_before and prev_streak >= 2 and freezes > 0:
        streak = prev_streak + 1     # missed exactly one day → a freeze saves it
        freezes -= 1
        insured = True
    else:
        streak = 1                   # missed 2+ days (or first ever) → reset
    # earn a freeze each time the streak hits a multiple of 7 (cap 2)
    earned_freeze = False
    if streak % 7 == 0 and freezes < 2:
        freezes += 1
        earned_freeze = True

    set_fields = {"login_streak": streak, "streak_freezes": freezes}
    extras = []

    # ── comeback bonus (returning after a long lapse) ─────────────────────────
    comeback = 1.0 if _gap_days(prev_daily) >= 7 else 0.0
    if comeback:
        extras.append(f"👋 <b>Welcome back!</b> A <code>+{fmt_amount(comeback)}</code> 💎 BGM comeback bonus is on us")

    # ── anniversary gift (yearly, on the join month-day) ──────────────────────
    anniv = 0.0
    joined = before.get("joined_at")
    now = datetime.now(timezone.utc)
    if hasattr(joined, "month"):
        # Feb-29 joiners celebrate on Feb-28 in non-leap years (else they'd only
        # get it every 4th year).
        anniv_day = (28 if joined.month == 2 and joined.day == 29
                     and not calendar.isleap(now.year) else joined.day)
        if (joined.month == now.month and now.day == anniv_day
                and now.year > joined.year and int(before.get("anniv_year") or 0) != now.year):
            anniv = 2.0
            set_fields["anniv_year"] = now.year
            extras.append(f"🎂 <b>Happy anniversary with us!</b> Enjoy a <code>+{fmt_amount(anniv)}</code> 💎 BGM gift")

    reward = _REWARDS[min(streak, 7) - 1]
    # sanitize keeps this credit inside the same [0, MAX_AMOUNT] guard the rest of
    # the economy enforces, even though the components are fixed constants today.
    total = sanitize_amount(round(reward + comeback + anniv, 3))
    # Credit + bookkeeping in ONE write so the reward and streak/freeze state
    # commit together (the atomic last_daily gate above already prevents a
    # double-claim).
    await db.safe_update("users", {"user_id": uid},
                         {"$set": set_fields, "$inc": {"bookgem": total}})

    if insured:
        extras.insert(0, "🛡 <b>Streak insurance</b> stepped in and rescued your streak")
    if earned_freeze:
        extras.append("🛡 You've earned a <b>streak freeze</b> — it quietly covers one missed day down the road")

    dots = "".join("🟢" if i < min(streak, 7) else "⚪" for i in range(7))
    extra_block = ("\n<blockquote>" + "\n".join(extras) + "</blockquote>\n") if extras else ""
    await message.answer(
        "🎁 <b>Daily Reward — Claimed</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <b>Day {streak}</b> of your reading streak\n{dots}\n"
        f"{extra_block}\n"
        f"💎 <b>+{fmt_amount(total)} BGM</b> added to your wallet\n"
        "<i>💡 Each day pays a little more — day 7 is the richest claim of all. See you tomorrow.</i>",
        reply_markup=kb([btn("💼 My Wallet", "acc_balance", style="primary"),
                         btn("🎡 Spin the Wheel", "daily_spin", style="success")]))

    # surface any freshly-earned achievements (e.g. streak milestones)
    from utils.achievements import check_unlocks
    await check_unlocks(message.bot, uid)
