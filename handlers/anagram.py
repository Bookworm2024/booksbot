"""
handlers/anagram.py — Word Anagram (chat-based, server-authoritative-ish).

🔀 Anagram → unscramble a shuffled literary word by typing it. Up to 3 tries,
a free first-letter 💡 Hint, or ⏭ Skip to reveal. Win → BGM (more for fewer
tries). 5 plays/day. The target word lives in the FSM (not sent unscrambled);
the reward is credited once (state is cleared before crediting).
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

_DAILY = 5
_MAX_TRIES = 3
_WORDS = [
    "NOVEL", "POETRY", "AUTHOR", "READER", "CHAPTER", "LIBRARY", "FICTION",
    "MYSTERY", "FANTASY", "ROMANCE", "STORY", "FABLE", "SONNET", "RHYME",
    "VERSE", "PROSE", "DRAMA", "COMEDY", "TRAGEDY", "LEGEND", "MYTH", "EPIC",
    "PLOT", "GENRE", "PREFACE", "VOLUME", "SEQUEL", "WIZARD", "HOBBIT", "DRAGON",
    "PIRATE", "DETECTIVE", "ROBOT", "GALAXY", "KINGDOM", "CASTLE", "QUEST",
    "RIDDLE", "SCROLL", "QUILL", "PARABLE", "FOLKLORE", "ANTHOLOGY", "MEMOIR",
]


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _scramble(word: str) -> str:
    letters = list(word)
    for _ in range(12):
        random.shuffle(letters)
        s = "".join(letters)
        if s != word:
            return s
    return word[::-1]


def _reward(tries: int) -> float:
    return round(max(0.1, 0.4 - 0.1 * tries), 2)


def _kbd():
    return kb([btn("💡 Hint", "anag_hint", style="primary"),
               btn("⏭ Skip", "anag_skip", style="danger")])


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"anag_day": 1, "anag_plays": 1}) or {}
    return int(u.get("anag_plays") or 0) if u.get("anag_day") == _today() else 0


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
        await send(f"🔀 <b>Anagram</b>\n\nDaily limit reached ({_DAILY}/day). Back tomorrow!",
                   reply_markup=kb([btn("🎮 Games", "menu_games", style="primary")]))
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"anag_day": _today(), "anag_plays": prev + 1}})
    word = random.choice(_WORDS)
    await state.set_state(AnagramFSM.answering)
    await state.update_data(word=word, tries=0, hinted=False)
    await send(f"🔀 <b>Word Anagram</b>\n━━━━━━━━━━━━━━━━━━\n"
               f"Unscramble this ({len(word)} letters):\n\n"
               f"<code>{' '.join(_scramble(word))}</code>\n\n"
               "✍️ Type your answer below.", reply_markup=_kbd())


class AnagramFSM(StatesGroup):
    answering = State()


@router.message(Command("anagram"))
async def cmd_anagram(message: Message, state: FSMContext) -> None:
    await _start(message, message.chat.id, state, edit=False)


@router.callback_query(F.data == "menu_anagram")
async def cb_open(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True)


@router.callback_query(F.data == "anag_new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True)


@router.callback_query(F.data == "anag_hint")
async def cb_hint(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    word = data.get("word")
    if not word:
        await call.answer("Start a new round.", show_alert=True)
        return
    await call.answer(f"Starts with: {word[0]}", show_alert=True)


@router.callback_query(F.data == "anag_skip")
async def cb_skip(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    word = data.get("word")
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        f"⏭ <b>Skipped.</b> The word was <b>{word or '—'}</b>.",
        reply_markup=kb([btn("🔀 New Word", "anag_new", style="success"),
                         btn("🎮 Games", "menu_games", style="primary")]))


@router.message(AnagramFSM.answering, F.text)
async def on_answer(message: Message, state: FSMContext) -> None:
    guess = (message.text or "").strip().upper()
    if guess == "/CANCEL":
        await state.clear(); await message.answer("❌ Cancelled."); return
    data = await state.get_data()
    word = data.get("word", "")
    tries = int(data.get("tries") or 0)

    if guess == word:
        await state.clear()   # clear BEFORE crediting → no double-credit
        rwd = _reward(tries)
        await add_bgm(message.chat.id, rwd)
        db = await MongoManager.get()
        await db.safe_update("users", {"user_id": message.chat.id},
                             {"$inc": {"games_played": 1, "game_bgm": rwd}})
        from utils.missions import mark
        await mark(message.chat.id, "play_game")
        await message.answer(
            f"🎉 <b>Correct — {word}!</b>\n💎 <b>+{fmt_amount(rwd)} BGM</b>",
            reply_markup=kb([btn("🔀 Play Again", "anag_new", style="success"),
                             btn("🎮 Games", "menu_games", style="primary")]))
        return

    tries += 1
    if tries >= _MAX_TRIES:
        await state.clear()
        await message.answer(
            f"❌ <b>Out of tries.</b> The word was <b>{word}</b>.",
            reply_markup=kb([btn("🔀 New Word", "anag_new", style="success"),
                             btn("🎮 Games", "menu_games", style="primary")]))
        return
    await state.update_data(tries=tries)
    await message.answer(f"❌ Not quite — try again ({tries}/{_MAX_TRIES}).\n"
                         f"<code>{' '.join(_scramble(word))}</code>", reply_markup=_kbd())
