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
from utils.keyboards import btn, kb

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
        prog = f"{'🟩' * bars}{'⬜' * (10 - bars)} <b>{done}/{goal}</b> ({pct}%)"
        if done >= goal:
            prog += "\n🎉 <b>Goal reached — amazing!</b>"
    else:
        prog = "No goal set yet — set one to track your year!"

    text = (f"🎯 <b>Reading Goal · {year}</b>\n━━━━━━━━━━━━━━━━━━\n{prog}\n\n"
            f"📚 <b>{year} Wrap-up</b>\n"
            f"📥 Books this year: <b>{done}</b>\n"
            f"📅 Days read: <b>{days}</b> · ⭐ Favorites: <b>{favs}</b>\n"
            f"🏷 Top genres: {top}")
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
    await call.message.edit_text("🎯 How many books do you want to read this year? "
                                 "Send a number (1–999). /cancel to abort.")


@router.message(GoalFSM.setting, F.text)
async def on_set(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    if not raw.isdigit() or not (1 <= int(raw) <= 999):
        await message.answer("⚠️ Send a whole number between 1 and 999.")
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": message.chat.id},
                         {"$set": {"read_goal": int(raw)}})
    text, markup = await _view(message.chat.id)
    await message.answer(f"✅ Goal set: <b>{raw}</b> books this year!\n\n" + text,
                         reply_markup=markup)
