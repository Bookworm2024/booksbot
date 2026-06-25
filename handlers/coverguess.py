"""
handlers/coverguess.py — Cover-Guess (emoji → book title), chat-based.

🎭 Cover Guess → a famous book is shown as an emoji "cover"; type the title.
Up to 3 tries, a free 💡 Hint (author + year) or ⏭ Skip to reveal. Win → BGM
(more for fewer tries). 5 plays/day. The answer lives in the FSM (never sent),
the reward is credited once (state cleared before crediting). Mirrors the
hangman/anagram pattern.
"""
import difflib
import logging
import random
import re
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

# (emoji cover, canonical title, hint, [accepted aliases])
_BOOKS = [
    ("🧙‍♂️💍🌋", "The Lord of the Rings", "J.R.R. Tolkien · 1954",
     ["lord of the rings", "lotr", "fellowship of the ring"]),
    ("⚡🧙📚", "Harry Potter", "J.K. Rowling · 1997",
     ["harry potter", "philosophers stone", "sorcerers stone"]),
    ("🦁🧥🚪❄️", "The Lion, the Witch and the Wardrobe", "C.S. Lewis · 1950",
     ["narnia", "the lion the witch and the wardrobe", "chronicles of narnia"]),
    ("🐋⚓🌊", "Moby Dick", "Herman Melville · 1851", ["moby dick", "moby-dick"]),
    ("🏝️🧭🪙", "Treasure Island", "R.L. Stevenson · 1883", ["treasure island"]),
    ("🐷🐑🚜", "Animal Farm", "George Orwell · 1945", ["animal farm"]),
    ("👁️1️⃣9️⃣8️⃣4️⃣", "1984", "George Orwell · 1949", ["1984", "nineteen eighty four"]),
    ("👧🐰🕳️🎩", "Alice in Wonderland", "Lewis Carroll · 1865",
     ["alice in wonderland", "alices adventures in wonderland"]),
    ("🎩🍫🏭", "Charlie and the Chocolate Factory", "Roald Dahl · 1964",
     ["charlie and the chocolate factory", "willy wonka"]),
    ("🧛🦇🏰", "Dracula", "Bram Stoker · 1897", ["dracula"]),
    ("🧟‍♂️⚡🔬", "Frankenstein", "Mary Shelley · 1818", ["frankenstein"]),
    ("🕵️🔍🚬", "Sherlock Holmes", "Arthur Conan Doyle · 1887",
     ["sherlock holmes", "a study in scarlet", "hound of the baskervilles"]),
    ("🐉🗡️👑❄️", "A Song of Ice and Fire", "George R.R. Martin · 1996",
     ["game of thrones", "a song of ice and fire", "a game of thrones"]),
    ("👻🎄💰", "A Christmas Carol", "Charles Dickens · 1843", ["a christmas carol"]),
    ("🐙🚢🌊2️⃣0️⃣", "Twenty Thousand Leagues Under the Sea", "Jules Verne · 1870",
     ["20000 leagues under the sea", "twenty thousand leagues under the sea"]),
    ("🎈🤡🚇", "It", "Stephen King · 1986", ["it"]),
    ("🐦🔫⚖️", "To Kill a Mockingbird", "Harper Lee · 1960", ["to kill a mockingbird"]),
    ("🧒🏝️🐚🔥", "Lord of the Flies", "William Golding · 1954", ["lord of the flies"]),
    ("🐊⏰🧚‍♀️", "Peter Pan", "J.M. Barrie · 1911", ["peter pan"]),
    ("🌹🤴🪐", "The Little Prince", "Antoine de Saint-Exupéry · 1943", ["the little prince"]),
    ("🦖🏝️🧬", "Jurassic Park", "Michael Crichton · 1990", ["jurassic park"]),
    ("🐝📓🍯", "Winnie the Pooh", "A.A. Milne · 1926", ["winnie the pooh"]),
    ("💍💃🏛️", "Pride and Prejudice", "Jane Austen · 1813", ["pride and prejudice"]),
    ("🦗🌽🚜🏜️", "The Grapes of Wrath", "John Steinbeck · 1939", ["the grapes of wrath"]),
    ("🎀🐀🦉🕷️", "Charlotte's Web", "E.B. White · 1952", ["charlottes web"]),
    ("🐺🔔🗡️", "The Call of the Wild", "Jack London · 1903", ["the call of the wild"]),
    ("🪑🎢🍫", "Matilda", "Roald Dahl · 1988", ["matilda"]),
    ("🟢🍳🥓", "Green Eggs and Ham", "Dr. Seuss · 1960", ["green eggs and ham"]),
]

_NORM_RE = re.compile(r"[^a-z0-9 ]+")
_STOP_RE = re.compile(r"\b(the|a|an|of|and)\b")


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _norm(s: str) -> str:
    s = _NORM_RE.sub(" ", (s or "").lower())
    s = _STOP_RE.sub(" ", s)
    return " ".join(s.split())


def _reward(tries: int) -> float:
    return round(max(0.1, 0.4 - 0.1 * tries), 2)


def _accepts(title: str, aliases: list[str]) -> set[str]:
    out = {_norm(title)}
    out.update(_norm(a) for a in aliases)
    return {a for a in out if a}


def _matches(guess: str, accepts: set[str]) -> bool:
    g = _norm(guess)
    if not g:
        return False
    if g in accepts:
        return True
    # tolerate small typos against any accepted form
    return any(difflib.SequenceMatcher(None, g, a).ratio() >= 0.86 for a in accepts)


def _kbd():
    return kb([btn("💡 Hint", "cg_hint", style="primary"),
               btn("⏭ Skip", "cg_skip", style="danger")])


def _again_kb():
    return kb([btn("🎭 Play Again", "cg_new", style="success"),
               btn("🎮 Games", "menu_games", style="primary")])


class CoverFSM(StatesGroup):
    answering = State()


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"cg_day": 1, "cg_plays": 1}) or {}
    return int(u.get("cg_plays") or 0) if u.get("cg_day") == _today() else 0


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
        await send(f"🎭 <b>Cover Guess</b>\n\nDaily limit reached ({_DAILY}/day). Back tomorrow!",
                   reply_markup=kb([btn("🎮 Games", "menu_games", style="primary")]))
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"cg_day": _today(), "cg_plays": prev + 1}})
    emojis, title, hint, aliases = random.choice(_BOOKS)
    await state.set_state(CoverFSM.answering)
    await state.update_data(title=title, hint=hint, emojis=emojis,
                            accepts=list(_accepts(title, aliases)), tries=0)
    await send(f"🎭 <b>Cover Guess</b>\n━━━━━━━━━━━━━━━━━━\n"
               f"Which book is this?\n\n<b>{emojis}</b>\n\n"
               "✍️ Type the title below.", reply_markup=_kbd())


@router.message(Command("coverguess"))
async def cmd_cover(message: Message, state: FSMContext) -> None:
    await _start(message, message.chat.id, state, edit=False)


@router.callback_query(F.data == "menu_coverguess")
async def cb_open(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True)


@router.callback_query(F.data == "cg_new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True)


@router.callback_query(F.data == "cg_hint")
async def cb_hint(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    hint = data.get("hint")
    if not hint:
        await call.answer("Start a new round.", show_alert=True)
        return
    await call.answer(f"✍️ {hint}", show_alert=True)


@router.callback_query(F.data == "cg_skip")
async def cb_skip(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    title = data.get("title")
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        f"⏭ <b>Skipped.</b> It was <b>{title or '—'}</b>.", reply_markup=_again_kb())


@router.message(CoverFSM.answering, F.text)
async def on_answer(message: Message, state: FSMContext) -> None:
    guess = (message.text or "").strip()
    if guess.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    data = await state.get_data()
    title = data.get("title", "")
    accepts = set(data.get("accepts") or [])
    tries = int(data.get("tries") or 0)

    if _matches(guess, accepts):
        await state.clear()   # clear BEFORE crediting → no double-credit
        rwd = _reward(tries)
        await add_bgm(message.chat.id, rwd)
        db = await MongoManager.get()
        await db.safe_update("users", {"user_id": message.chat.id},
                             {"$inc": {"games_played": 1, "game_bgm": rwd}})
        from utils.missions import mark
        await mark(message.chat.id, "play_game")
        await message.answer(
            f"🎉 <b>Correct — {title}!</b>\n💎 <b>+{fmt_amount(rwd)} BGM</b>",
            reply_markup=_again_kb())
        return

    tries += 1
    if tries >= _MAX_TRIES:
        await state.clear()
        await message.answer(
            f"❌ <b>Out of tries.</b> It was <b>{title}</b>.", reply_markup=_again_kb())
        return
    await state.update_data(tries=tries)
    await message.answer(f"❌ Not quite — try again ({tries}/{_MAX_TRIES}).",
                         reply_markup=_kbd())
