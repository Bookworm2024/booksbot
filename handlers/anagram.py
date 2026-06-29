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
from utils.games import daily_limit
from utils.keyboards import btn, kb
from utils.premium import is_premium
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()

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
    return kb([btn("💡 Reveal First Letter", "anag_hint", style="primary"),
               btn("⏭ Skip & Reveal", "anag_skip", style="danger")])


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"anag_day": 1, "anag_plays": 1}) or {}
    return int(u.get("anag_plays") or 0) if u.get("anag_day") == _today() else 0


async def _start(message: Message, uid: int, state: FSMContext, *, edit: bool) -> None:
    from utils.flags import is_on
    send = message.edit_text if edit else message.answer
    if not await is_on("games"):
        await send("⏳ <b>Games are taking a short break</b>\n"
                   "━━━━━━━━━━━━━━━━━━━━\n"
                   "<blockquote>Our game room is being polished right now. It'll be back shortly — "
                   "your library and rewards are untouched in the meantime.</blockquote>\n"
                   "<i>💡 Check back soon — there's BGM waiting to be won.</i>",
                   reply_markup=kb([btn("🔙 Back to Menu", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    lim = await daily_limit(uid)
    prev = await _plays_today(db, uid)
    if prev >= lim:
        free = not await is_premium(uid)
        txt = (f"🎮 <b>Word Anagram</b>\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"⏳ <b>You've played today's full set.</b>\n"
               f"<blockquote>You've used all <code>{lim}</code> rounds for today — well played. "
               f"Your puzzles refresh at midnight for a brand-new run.\n\n"
               f"<i>Plenty more rewards waiting across the Games Hub in the meantime.</i></blockquote>\n"
               f"<i>💡 Come back tomorrow to unscramble a fresh set.</i>")
        rows = []
        if free:
            txt += "\n<i>👑 Premium plays 5 rounds a day, every game.</i>"
            rows.append([btn("👑 Go Premium for 5/day", "go_premium", style="success")])
        rows.append([btn("🎮 Games Hub", "menu_games", style="primary")])
        await send(txt, reply_markup=kb(*rows))
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"anag_day": _today(), "anag_plays": prev + 1}})
    word = random.choice(_WORDS)
    await state.set_state(AnagramFSM.answering)
    await state.update_data(word=word, tries=0, hinted=False)
    await send(f"🎮 <b>Word Anagram</b>\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"<i>One bookish word, letters shuffled — set them back in order to win 💎 BGM.</i>\n"
               f"<blockquote>🔀 <b>Unscramble these {len(word)} letters:</b>\n\n"
               f"<code>{' '.join(_scramble(word))}</code></blockquote>\n"
               f"✍️ <i>Type your answer below — you have <b>{_MAX_TRIES}</b> tries. "
               f"Solve it fast for the biggest reward.</i>", reply_markup=_kbd())


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
        await call.answer("This round has ended. Tap New Word to start a fresh puzzle.", show_alert=True)
        return
    await call.answer(f"💡 Here's a nudge — the word begins with: {word[0]}", show_alert=True)


@router.callback_query(F.data == "anag_skip")
async def cb_skip(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    word = data.get("word")
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        f"⏭ <b>Round revealed</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>The word was <b>{word or '—'}</b>.\n"
        f"<i>One to file away — these words love to come back around.</i></blockquote>\n"
        f"<i>💡 Line up a fresh scramble and go for the reward.</i>",
        reply_markup=kb([btn("🔀 New Word", "anag_new", style="success"),
                         btn("🎮 Games Hub", "menu_games", style="primary")]))


@router.message(AnagramFSM.answering, F.text)
async def on_answer(message: Message, state: FSMContext) -> None:
    guess = (message.text or "").strip().upper()
    if guess == "/CANCEL":
        await state.clear(); await message.answer(
            "✅ <b>Round closed.</b> <i>Your puzzle's set aside — start a new one whenever you're ready.</i>"); return
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
            f"✨ <b>Correct — it was {word}!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>Sharp solve. 🎁 <i>Reward credited:</i> 💎 <b>+{fmt_amount(rwd)} BGM</b> — "
            f"already in your wallet.</blockquote>\n"
            f"<i>💡 The fewer tries you take, the bigger the reward. Ready for another?</i>",
            reply_markup=kb([btn("🔀 Play Again", "anag_new", style="success"),
                             btn("🎮 Games Hub", "menu_games", style="primary")]))
        return

    tries += 1
    if tries >= _MAX_TRIES:
        await state.clear()
        await message.answer(
            f"💀 <b>Out of tries — that one stayed scrambled.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>The word was <b>{word}</b>.\n"
            f"<i>No harm done — every puzzle trains your eye for the next.</i></blockquote>\n"
            f"<i>💡 Fresh scramble, fresh shot at the reward.</i>",
            reply_markup=kb([btn("🔀 New Word", "anag_new", style="success"),
                             btn("🎮 Games Hub", "menu_games", style="primary")]))
        return
    await state.update_data(tries=tries)
    left = _MAX_TRIES - tries
    await message.answer(f"❌ <b>Not quite — keep going.</b> "
                         f"<i>{left} {'try' if left == 1 else 'tries'} left ({tries}/{_MAX_TRIES} used).</i>\n"
                         f"<blockquote><code>{' '.join(_scramble(word))}</code></blockquote>\n"
                         f"✍️ <i>Type your next answer below.</i>", reply_markup=_kbd())
