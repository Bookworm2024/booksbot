"""
handlers/speedread.py — Speed-Reading WPM challenge (chat-based).

⚡ Speed Read → read a short passage, tap ✅ Done, and the bot times you to
compute your words-per-minute. Then a one-tap comprehension question gates the
reward (so you can't just skim-and-tap). Win → BGM scaled by speed. 3 plays/day.

Timing is server-side (started_at stored when the passage is shown); the correct
answer lives in the FSM and is never sent to the client.
"""
import logging
import random
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()

_DAILY = 3
_CHEAT_WPM = 2000   # above this the "Done" tap was instant → not a real read

# (passage, question, [options], correct_index)
_PASSAGES = [
    ("The old lighthouse keeper had not seen another soul in three winters. Every "
     "evening he climbed the spiral stair, lit the great lamp, and watched its beam "
     "sweep the black water for ships that never came. He kept a journal of the "
     "weather and the gulls, and on the loneliest nights he read aloud to the empty "
     "room, just to hear a human voice answer the sea.",
     "How long had the keeper gone without seeing another person?",
     ["Three winters", "A single night", "Ten years", "Two days"], 0),
    ("Maya pressed the seed into the dark soil and covered it gently. Her grandmother "
     "had told her that patience was the only fertilizer that truly mattered. For weeks "
     "nothing happened, and Maya nearly gave up. Then one grey morning a pale green "
     "shoot broke the surface, no taller than her thumb, reaching stubbornly toward the "
     "thin light of the window.",
     "What did Maya's grandmother say truly mattered?",
     ["Sunlight", "Patience", "Expensive soil", "Daily watering"], 1),
    ("The market opened at dawn in a riot of colour and noise. Vendors stacked oranges "
     "into bright pyramids, hammered copper pots rang like bells, and the smell of warm "
     "bread drifted between the stalls. A small boy weaved through the crowd carrying a "
     "tray of mint tea, calling out prices in a voice far bigger than himself, never "
     "spilling a single glass.",
     "What was the boy carrying through the crowd?",
     ["A tray of mint tea", "A basket of oranges", "Copper pots", "Fresh bread"], 0),
    ("Captain Reyes studied the storm on the horizon and made a decision the crew would "
     "remember for years. Rather than run for the distant harbour, she turned the ship "
     "directly into the wind, trimmed the sails to a sliver, and rode the towering swells "
     "at an angle. By midnight the worst had passed, and not a single barrel had been "
     "lost overboard.",
     "What did Captain Reyes decide to do about the storm?",
     ["Turn into the wind and ride it", "Race to the harbour",
      "Drop anchor and wait", "Abandon ship"], 0),
    ("Inventions rarely arrive fully formed. The first printing press was a clumsy "
     "marriage of a wine press and a coin punch, and early books were slow, smudged, "
     "and wildly expensive. Yet within fifty years the design spread across a continent, "
     "and ideas that once travelled at the speed of a walking monk could suddenly leap "
     "from city to city in printed sheets.",
     "The first printing press borrowed from which two devices?",
     ["A wine press and a coin punch", "A loom and a plough",
      "A clock and a mill", "A forge and a bell"], 0),
    ("Deep in the rainforest, a single fig tree can feed a hundred species. Its fruit "
     "ripens unpredictably, so birds, monkeys, and insects must visit again and again, "
     "carrying seeds far beyond the parent tree. Scientists call such species keystones, "
     "because removing them can quietly collapse an entire web of life that depended on "
     "their generous, year-round harvest.",
     "Why are species like the fig tree called keystones?",
     ["Removing them can collapse the food web", "They are the tallest trees",
      "They live the longest", "They have the hardest wood"], 0),
]


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _wpm(words: int, seconds: float) -> int:
    if seconds <= 0:
        return _CHEAT_WPM + 1
    return int(round(words / (seconds / 60.0)))


def _reward(wpm: int, correct: bool) -> float:
    if not correct:
        return 0.0
    # base for understanding + a capped speed bonus
    return round(min(0.5, 0.15 + min(wpm, 600) / 2000.0), 2)


def _again_kb():
    return kb([btn("⚡ Play Again", "sr_new", style="success"),
               btn("🎮 Games", "menu_games", style="primary")])


class SpeedFSM(StatesGroup):
    reading = State()
    answering = State()


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"sr_day": 1, "sr_plays": 1}) or {}
    return int(u.get("sr_plays") or 0) if u.get("sr_day") == _today() else 0


async def _start(message: Message, uid: int, state: FSMContext, *, edit: bool) -> None:
    from utils.flags import is_on
    send = message.edit_text if edit else message.answer
    if not await is_on("games"):
        await send("🎮 <b>Games are paused</b> — check back soon!",
                   reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    prev = await _plays_today(db, uid)
    if prev >= _DAILY:
        await send(f"⚡ <b>Speed Read</b>\n\nDaily limit reached ({_DAILY}/day). Back tomorrow!",
                   reply_markup=kb([btn("🎮 Games", "menu_games", style="primary")]))
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"sr_day": _today(), "sr_plays": prev + 1}})
    idx = random.randrange(len(_PASSAGES))
    passage, question, options, answer = _PASSAGES[idx]
    words = len(passage.split())
    await state.set_state(SpeedFSM.reading)
    await state.update_data(words=words, question=question, options=options,
                            answer=answer, started=_now().timestamp())
    await send(
        "⚡ <b>Speed Read</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Read this passage, then tap <b>✅ Done</b>. ({words} words)\n\n"
        f"<blockquote>{passage}</blockquote>\n\n"
        "⏱ Timing starts now — a comprehension question follows.",
        reply_markup=kb([btn("✅ Done Reading", "sr_done", style="success")]))


@router.message(Command("speedread"))
async def cmd_speed(message: Message, state: FSMContext) -> None:
    await _start(message, message.chat.id, state, edit=False)


@router.callback_query(F.data == "menu_speedread")
async def cb_open(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True)


@router.callback_query(F.data == "sr_new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True)


@router.callback_query(SpeedFSM.reading, F.data == "sr_done")
async def cb_done(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    started = float(data.get("started") or 0)
    words = int(data.get("words") or 0)
    elapsed = max(0.0, _now().timestamp() - started)
    wpm = _wpm(words, elapsed)
    options = data.get("options") or []
    await state.set_state(SpeedFSM.answering)
    await state.update_data(wpm=wpm)
    rows = [[btn(f"{chr(65 + i)}. {opt}", f"sr:{i}", style="primary")]
            for i, opt in enumerate(options)]
    await call.message.edit_text(
        f"⏱ <b>{wpm} WPM</b> ({words} words in {elapsed:.1f}s)\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📖 <b>Comprehension check:</b>\n{data.get('question', '')}",
        reply_markup=kb(*rows))


@router.callback_query(SpeedFSM.answering, F.data.startswith("sr:"))
async def cb_answer(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        pick = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    answer = int(data.get("answer") or 0)
    wpm = int(data.get("wpm") or 0)
    options = data.get("options") or []
    await state.clear()   # clear BEFORE crediting → no double-credit
    correct = pick == answer
    cheated = wpm > _CHEAT_WPM
    rwd = 0.0 if cheated else _reward(wpm, correct)
    await call.answer("✅ Correct!" if correct else "❌ Not quite")
    if rwd > 0:
        await add_bgm(call.from_user.id, rwd)
        db = await MongoManager.get()
        await db.safe_update("users", {"user_id": call.from_user.id},
                             {"$inc": {"games_played": 1, "game_bgm": rwd}})
        from utils.missions import mark
        await mark(call.from_user.id, "play_game")
    right = options[answer] if 0 <= answer < len(options) else "—"
    if cheated:
        verdict = "⚡ Too fast to be a real read — no reward this time."
    elif correct:
        verdict = f"🎉 <b>Correct!</b> Speed <b>{wpm} WPM</b>\n💎 <b>+{fmt_amount(rwd)} BGM</b>"
    else:
        verdict = f"❌ <b>Wrong.</b> The answer was <b>{right}</b>."
    await call.message.edit_text(
        f"⚡ <b>Speed Read — Result</b>\n━━━━━━━━━━━━━━━━━━\n{verdict}",
        reply_markup=_again_kb())
