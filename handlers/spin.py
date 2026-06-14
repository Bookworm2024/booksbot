"""
handlers/spin.py — daily Spin-the-Wheel (retention/growth).

Once per day, free, server-side: a weighted random BGM prize. Tracked by
last_spin date on the user doc, so it can't be farmed.
"""
import logging
import random
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, kb
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()

# (BGM prize, weight) — small wins common, jackpot rare
_PRIZES = [(0.05, 30), (0.1, 25), (0.25, 18), (0.5, 12), (1.0, 9), (2.0, 5), (5.0, 1)]
_BAG = [p for p, w in _PRIZES for _ in range(w)]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@router.message(Command("spin"))
async def cmd_spin(message: Message) -> None:
    await _spin(message, message.chat.id)


@router.callback_query(F.data == "daily_spin")
async def cb_spin(call: CallbackQuery) -> None:
    await call.answer()
    await _spin(call.message, call.from_user.id)


async def _spin(message: Message, uid: int) -> None:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"last_spin": 1}) or {}
    if doc.get("last_spin") == _today():
        await message.answer(
            "🎡 <b>Already spun today!</b>\nCome back tomorrow for another free spin.",
            reply_markup=kb([btn("🎮 Play Games", "menu_games", style="success")]))
        return
    # claim the spin atomically (only first call today wins)
    claimed = await db.find_one_and_update_global(
        "users", {"user_id": uid, "last_spin": {"$ne": _today()}},
        {"$set": {"last_spin": _today()}})
    if not claimed:
        await message.answer("🎡 Already spun today — see you tomorrow!")
        return
    prize = random.choice(_BAG)
    await add_bgm(uid, prize)
    from utils.missions import mark
    await mark(uid, "spin")
    jackpot = "🎉 <b>JACKPOT!</b>\n" if prize >= 5 else ""
    await message.answer(
        f"🎡 <b>Daily Spin</b>\n━━━━━━━━━━━━━━━━━━\n{jackpot}"
        f"You won <b>+{prize:g} BGM</b>! 💎\n\n<i>Spin again tomorrow.</i>",
        reply_markup=kb([btn("💼 Balance", "acc_balance", style="primary"),
                         btn("🎮 Games", "menu_games", style="success")]))
