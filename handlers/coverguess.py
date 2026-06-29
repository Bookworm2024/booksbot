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
import uuid
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
    return kb([btn("💡 Reveal Hint", "cg_hint", style="primary"),
               btn("⏭ Skip & Reveal", "cg_skip", style="danger")])


def _again_kb():
    return kb([btn("🎭 Play Another", "cg_new", style="success"),
               btn("🎮 Games Lounge", "menu_games", style="primary")])


class CoverFSM(StatesGroup):
    answering = State()


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"cg_day": 1, "cg_plays": 1}) or {}
    return int(u.get("cg_plays") or 0) if u.get("cg_day") == _today() else 0


async def _consume_play(db, uid: int, lim: int) -> bool:
    """Atomically count one play under the daily cap (race-safe, like memory.py).
    Mirrors utils/quota.py's sentinels: lim <= 0 is closed, lim < 0 is unlimited."""
    if lim == 0:
        return False
    today = _today()
    reset = await db.find_one_and_update_global(
        "users", {"user_id": uid, "cg_day": {"$ne": today}},
        {"$set": {"cg_day": today, "cg_plays": 1}})
    if reset is not None:
        return True
    if lim < 0:  # unlimited: bump the counter for stats and always allow
        await db.find_one_and_update_global(
            "users", {"user_id": uid, "cg_day": today}, {"$inc": {"cg_plays": 1}})
        return True
    inc = await db.find_one_and_update_global(
        "users", {"user_id": uid, "cg_day": today, "cg_plays": {"$lt": lim}},
        {"$inc": {"cg_plays": 1}})
    return inc is not None


async def _start(message: Message, uid: int, state: FSMContext, *, edit: bool) -> None:
    from utils.flags import is_on
    send = message.edit_text if edit else message.answer
    if not await is_on("games"):
        await send(
            "🎮 <b>Games Lounge — Resting</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Our games are taking a short intermission while we polish "
            "them up. Your library stays open in the meantime — search a title, open "
            "the reader, or check Discover for something new.</blockquote>\n"
            "<i>💡 Do check back soon — the tables reopen shortly.</i>",
            reply_markup=kb([btn("🔙 Back to Menu", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    lim = await daily_limit(uid)
    if not await _consume_play(db, uid, lim):
        free = not await is_premium(uid)
        txt = (
            "🎭 <b>Cover Guess — Come Back Tomorrow</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>You've played all <code>{lim}</code> rounds for today — "
            "nicely done. Your plays refresh at midnight, ready for another run "
            "at the shelf.</blockquote>\n"
            "<i>💡 In the meantime, the other games are open — pick one from the "
            "lounge.</i>")
        rows = []
        if free:
            txt += "\n<i>👑 Premium plays 5 rounds a day, every game.</i>"
            rows.append([btn("👑 Go Premium for 5/day", "go_premium", style="success")])
        rows.append([btn("🎮 Games Lounge", "menu_games", style="primary")])
        await send(txt, reply_markup=kb(*rows))
        return
    emojis, title, hint, aliases = random.choice(_BOOKS)
    await state.set_state(CoverFSM.answering)
    # per-round token → atomic single-winner reward claim (FSM isolation is off,
    # so state.clear() alone can't dedup a fast double-send of the right answer).
    await state.update_data(title=title, hint=hint, emojis=emojis,
                            accepts=list(_accepts(title, aliases)), tries=0,
                            cg_round=uuid.uuid4().hex)
    await send(
        "🎭 <b>Cover Guess</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>One famous book, dressed up in emoji. Can you name it?</i>\n\n"
        f"<b>{emojis}</b>\n"
        "<blockquote>You have <code>3</code> guesses. Stuck? Tap 💡 for a free hint "
        "(author and year), or ⏭ to reveal the answer. Crack it on the first try "
        "for the biggest 💎 BGM reward.</blockquote>\n"
        "<i>✍️ Type the title below to make your guess.</i>",
        reply_markup=_kbd())


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
        await call.answer(
            "This round has wrapped up. Tap Play Another to start a fresh cover.",
            show_alert=True)
        return
    await call.answer(f"💡 Here's your hint — {hint}", show_alert=True)


@router.callback_query(F.data == "cg_skip")
async def cb_skip(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    title = data.get("title")
    await state.clear()
    await call.answer()
    await call.message.edit_text(
        "🎭 <b>Cover Guess — Revealed</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>The cover was hiding <b>{title or '—'}</b>. One to add to "
        "your reading list, perhaps.</blockquote>\n"
        "<i>💡 Ready for another? A fresh cover is waiting.</i>",
        reply_markup=_again_kb())


@router.message(CoverFSM.answering, F.text)
async def on_answer(message: Message, state: FSMContext) -> None:
    guess = (message.text or "").strip()
    if guess.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "🎭 <b>Cover Guess — Closed</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>No worries — this round is set aside. The lounge is here whenever "
            "you'd like another go.</i>")
        return
    data = await state.get_data()
    title = data.get("title", "")
    accepts = set(data.get("accepts") or [])
    tries = int(data.get("tries") or 0)

    if _matches(guess, accepts):
        await state.clear()
        # Gate the reward on an atomic per-round-token claim: only the task that
        # flips cg_solved_token to this round's token pays out, so a fast
        # double-send of the right title can't credit BGM twice (FSM isolation
        # is off → state.clear() is not a reliable dedup guard).
        rwd = _reward(tries)
        db = await MongoManager.get()
        rt = data.get("cg_round") or uuid.uuid4().hex
        won = await db.find_one_and_update_global(
            "users", {"user_id": message.chat.id, "cg_solved_token": {"$ne": rt}},
            {"$set": {"cg_solved_token": rt}})
        if won is not None:
            await add_bgm(message.chat.id, rwd)
            await db.safe_update("users", {"user_id": message.chat.id},
                                 {"$inc": {"games_played": 1, "game_bgm": rwd}})
            from utils.missions import mark
            await mark(message.chat.id, "play_game")
        await message.answer(
            "✨ <b>Spot On!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>That cover was <b>{title}</b> — beautifully read.\n"
            f"💎 <b>+{fmt_amount(rwd)} BGM</b> has landed in your wallet.</blockquote>\n"
            "<i>💡 On a roll? Line up the next cover.</i>",
            reply_markup=_again_kb())
        return

    tries += 1
    if tries >= _MAX_TRIES:
        await state.clear()
        await message.answer(
            "🎭 <b>Cover Guess — Revealed</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>That's your last guess for this one — it was "
            f"<b>{title}</b>. A worthy addition to any shelf.</blockquote>\n"
            "<i>💡 Shake it off — a brand-new cover is one tap away.</i>",
            reply_markup=_again_kb())
        return
    await state.update_data(tries=tries)
    await message.answer(
        "❌ <b>Not this one</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Close, but not the title we're after. You have "
        f"<code>{_MAX_TRIES - tries}</code> guess(es) left.</blockquote>\n"
        "<i>💡 Need a nudge? Tap 💡 for a free hint, then ✍️ type your next "
        "guess.</i>",
        reply_markup=_kbd())
