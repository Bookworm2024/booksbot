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
        await message.answer(
            "⏳ <b>Thanks for the love</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>You've shared <code>3</code> ratings today — that's our daily "
            "limit, and we've heard you loud and clear. The form reopens tomorrow.\n\n"
            "<i>💡 Spotted a problem in the meantime? Use /report and we'll take it "
            "from there.</i></blockquote>")
        return
    rows, row = [], []
    for i in range(1, 11):
        emoji = "😞" if i <= 3 else "🙂" if i <= 7 else "🤩"
        row.append(btn(f"{emoji}{i}", f"rate_set:{i}", style="primary"))
        if len(row) == 5:
            rows.append(row)
            row = []
    rows.append([btn("❌ Cancel", "menu_home", style="danger")])
    await message.answer(
        "⭐ <b>How are we doing?</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Your honest take shapes what we build next.</i>\n\n"
        "<blockquote>Tap a score from <b>1</b> to <b>10</b> — <code>1</code> means "
        "we let you down, <code>10</code> means we nailed it. Every rating reaches "
        "the team directly.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("rate_set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    rating = int(call.data.split(":", 1)[1])
    await state.set_state(RateFSM.awaiting_comment)
    await state.update_data(rating=rating)
    await call.answer(f"You rated us {rating}/10 — thank you!")
    await call.message.edit_text(
        f"⭐ <b>You scored us {rating}/10</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Noted with thanks — one more step, if you have a moment.</i>\n\n"
        f"<blockquote>What made it a <b>{rating}</b>? A line on what you loved — or "
        "what we could do better — tells us exactly where to focus. Send it as a "
        "message, or skip below.</blockquote>",
        reply_markup=kb([btn("⏭ Skip & Submit", "rate_skip", style="primary")]))


@router.callback_query(F.data == "rate_skip")
async def cb_skip(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await call.answer()
    await _submit(call.bot, call.from_user, data.get("rating", 0), "")
    await call.message.edit_text(
        "✨ <b>Rating received</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Thank you — your score is on its way to the team. "
        "Every point helps us serve your library better.</i>")


@router.message(RateFSM.awaiting_comment, F.text)
async def on_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await _submit(message.bot, message.from_user, data.get("rating", 0),
                  (message.text or "")[:500])
    await message.answer(
        "✨ <b>Feedback received</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Thank you for the detail — your rating and notes are now with the team, "
        "and they genuinely shape what we build next.</i>")


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
