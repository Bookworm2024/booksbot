"""
handlers/daily.py — daily login-streak reward (retention).

Claim once/day; the reward grows with your consecutive-day streak (resets if you
miss a day), capping at day 7. Atomic claim so it can't be double-collected.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, kb
from utils.wallet import add_bgm

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


async def _claim(message: Message, uid: int) -> None:
    db = await MongoManager.get()
    today, yesterday = _d(0), _d(-1)
    # atomic: only the first claim today flips last_daily
    before = await db.find_one_and_update_global(
        "users", {"user_id": uid, "last_daily": {"$ne": today}},
        {"$set": {"last_daily": today}}, return_before=True)
    if before is None:
        doc = await db.find_one_global("users", {"user_id": uid}, {"login_streak": 1}) or {}
        await message.answer(
            "🎁 <b>Already claimed today!</b>\n"
            f"🔥 Current streak: <b>{int(doc.get('login_streak') or 0)} day(s)</b>\n"
            "Come back tomorrow to keep it going.",
            reply_markup=kb([btn("💼 Balance", "acc_balance", style="primary")]))
        return
    prev_streak = int(before.get("login_streak") or 0)
    streak = prev_streak + 1 if before.get("last_daily") == yesterday else 1
    reward = _REWARDS[min(streak, 7) - 1]
    await db.safe_update("users", {"user_id": uid}, {"$set": {"login_streak": streak}})
    await add_bgm(uid, reward)
    dots = "".join("🟢" if i < min(streak, 7) else "⚪" for i in range(7))
    await message.answer(
        "🎁 <b>Daily Reward Claimed!</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <b>Day {streak}</b> streak\n{dots}\n\n"
        f"💎 <b>+{reward:g} BGM</b>\n<i>Keep the streak — day 7 pays the most!</i>",
        reply_markup=kb([btn("💼 Balance", "acc_balance", style="primary"),
                         btn("🎡 Daily Spin", "daily_spin", style="success")]))
