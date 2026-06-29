"""
handlers/goals.py — yearly reading goal + wrap-up.

📖 Library → 🎯 Reading Goal: set how many books you want to read this year and
track progress (downloads this year), plus a wrap-up (days read, favorites, top
genres). The per-year counter is bumped on each download (request.py).
"""
import logging
from collections import Counter
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, cancel_row, kb

logger = logging.getLogger(__name__)
router = Router()


def _year() -> int:
    return datetime.now(timezone.utc).year


class GoalFSM(StatesGroup):
    setting = State()


async def _view(uid: int):
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"read_goal": 1, "reads": 1, "reading_days": 1}) or {}
    year = _year()
    goal = int(u.get("read_goal") or 0)
    done = int((u.get("reads") or {}).get(str(year)) or 0)
    days = len(u.get("reading_days") or [])
    favs = await db.count_global("favorites", {"user_id": uid})
    # top genres from favorites
    fav_rows = await db.find_global("favorites", {"user_id": uid}, limit=500,
                                    proj={"file_unique_id": 1})
    fuids = [x["file_unique_id"] for x in fav_rows][:500]
    genres: Counter = Counter()
    if fuids:
        files = await db.find_global("files", {"file_unique_id": {"$in": fuids}},
                                     proj={"genre": 1})
        for f in files:
            genres[f.get("genre") or "Untagged"] += 1
    top = ", ".join(g for g, _ in genres.most_common(3)) or "—"

    if goal:
        pct = min(100, int(done / goal * 100))
        bars = pct // 10
        prog = (f"{'🟩' * bars}{'⬜' * (10 - bars)}\n"
                f"<b>{done}</b> of <b>{goal}</b> books  ·  <code>{pct}%</code> of the way there")
        if done >= goal:
            prog += "\n\n🎉 <b>Goal reached — what a year.</b> Every book from here is a bonus."
    else:
        prog = ("<i>No goal set yet.</i>\n"
                "Pick a number you'd love to reach this year and we'll track every "
                "book toward it for you.")

    text = (f"🎯 <b>Reading Goal · {year}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Your year in books, counted as you go.</i>\n\n"
            f"<blockquote>{prog}</blockquote>\n"
            f"📊 <b>Your {year} so far</b>\n"
            f"<blockquote>📥 Books this year:  <b>{done}</b>\n"
            f"📅 Days you read:  <b>{days}</b>\n"
            f"⭐ Favorites saved:  <b>{favs}</b>\n"
            f"🏷 Top genres:  {top}</blockquote>\n"
            "<i>💡 Each book you download this year nudges the bar a little higher.</i>")
    return text, kb([btn("🎯 Set / Change Goal", "goal_set", style="success")],
                    [btn("🔙 Library", "menu_library", style="danger")])


@router.message(Command("goal"))
async def cmd_goal(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "lib_goal")
async def cb_goal(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "goal_set")
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(GoalFSM.setting)
    await call.message.edit_text(
        "🎯 <b>Set Your Reading Goal</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>One number, and we'll track the whole year toward it.</i>\n\n"
        "<blockquote>How many books would you love to finish this year?\n\n"
        "Send any number from <code>1</code> to <code>999</code>. You can change it "
        "anytime.\n\n💡 Tap Cancel below to step back without setting one.</blockquote>",
        reply_markup=kb(cancel_row("menu_library")))


@router.message(GoalFSM.setting, F.text)
async def on_set(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ No problem — your goal is unchanged. Come back whenever "
                             "you're ready to set one.")
        return
    await state.clear()
    if not raw.isdigit() or not (1 <= int(raw) <= 999):
        await message.answer("⚠️ <b>That's not quite a goal yet</b>\n\n"
                             "<blockquote>Send a whole number between <code>1</code> and "
                             "<code>999</code> — that's your target for the year. "
                             "Try again, or tap Cancel below to step back.</blockquote>",
                             reply_markup=kb(cancel_row("menu_library")))
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": message.chat.id},
                         {"$set": {"read_goal": int(raw)}})
    text, markup = await _view(message.chat.id)
    await message.answer(f"✅ <b>Goal set — {raw} books this year.</b> "
                         "We'll count every one for you.\n\n" + text,
                         reply_markup=markup)
