"""
handlers/rate.py — user feedback / rating.

  /rate → 1–10 grid → optional comment → logged to admins + global average.
  Limit: 3 ratings per user per rolling 24h.
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, LOG_CHANNEL_ID
from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_DAILY_LIMIT = 3


class RateFSM(StatesGroup):
    awaiting_comment = State()


@router.message(Command("rate"))
async def cmd_rate(message: Message) -> None:
    await _open(message, message.chat.id)


@router.callback_query(F.data == "menu_rate")
async def cb_rate(call: CallbackQuery) -> None:
    await call.answer()
    await _open(call.message, call.from_user.id)


async def _open(message: Message, uid: int) -> None:
    db = await MongoManager.get()
    since = datetime.now(timezone.utc).timestamp() - 86400
    recent = await db.count_global("ratings",
                                   {"user_id": uid, "ts": {"$gte": since}})
    if recent >= _DAILY_LIMIT:
        await message.answer("⏳ You've reached today's rating limit (3). Try again tomorrow.")
        return
    rows, row = [], []
    for i in range(1, 11):
        emoji = "😞" if i <= 3 else "🙂" if i <= 7 else "🤩"
        row.append(btn(f"{emoji}{i}", f"rate_set:{i}", style="primary"))
        if len(row) == 5:
            rows.append(row)
            row = []
    rows.append([btn("❌ Cancel", "menu_home", style="danger")])
    await message.answer("⭐ <b>Rate your experience</b> (1–10):", reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("rate_set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    rating = int(call.data.split(":", 1)[1])
    await state.set_state(RateFSM.awaiting_comment)
    await state.update_data(rating=rating)
    await call.answer(f"You rated {rating}/10")
    await call.message.edit_text(
        f"⭐ <b>{rating}/10</b> — thanks!\n\nAdd a comment, or skip:",
        reply_markup=kb([btn("⏭ Skip", "rate_skip", style="primary")]))


@router.callback_query(F.data == "rate_skip")
async def cb_skip(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await call.answer()
    await _submit(call.bot, call.from_user, data.get("rating", 0), "")
    await call.message.edit_text("✅ <b>Feedback submitted.</b> Thank you!")


@router.message(RateFSM.awaiting_comment, F.text)
async def on_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await _submit(message.bot, message.from_user, data.get("rating", 0),
                  (message.text or "")[:500])
    await message.answer("✅ <b>Feedback submitted.</b> Thank you!")


async def _submit(bot, user, rating: int, comment: str) -> None:
    db = await MongoManager.get()
    await db.safe_insert("ratings", {
        "user_id": user.id, "rating": rating, "comment": comment,
        "ts": datetime.now(timezone.utc).timestamp(),
        "at": datetime.now(timezone.utc),
    })
    emoji = "🔴" if rating < 5 else "🟢" if rating > 7 else "🟡"
    log = (f"{emoji} <b>New Feedback</b> — {rating}/10\n"
           f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a> (<code>{user.id}</code>)\n"
           f"💬 {comment or '—'}")
    targets = set(ADMIN_IDS)
    if LOG_CHANNEL_ID:
        targets.add(LOG_CHANNEL_ID)
    for t in targets:
        try:
            await bot.send_message(t, log)
        except Exception:  # noqa: BLE001
            pass
