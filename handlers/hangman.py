"""
handlers/hangman.py — Literary Hangman (chat-based, server-authoritative).

🔤 Hangman → guess a hidden literary word letter-by-letter on an inline keyboard.
The word lives server-side (never sent to the client); 6 wrong guesses and it's
over. Win → BGM (more for fewer mistakes). 3 plays/day. State is in the
`hangman_games` collection so it survives restarts.
"""
import logging
import random
import string
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()

_ALPHA = string.ascii_uppercase
_MAX_WRONG = 6
_DAILY = 3

# Single-word, well-known literary terms / titles / author surnames (A–Z only).
_WORDS = [
    "NOVEL", "POETRY", "CHAPTER", "PROLOGUE", "EPILOGUE", "NARRATOR", "FICTION",
    "MYSTERY", "FANTASY", "ROMANCE", "THRILLER", "MEMOIR", "FABLE", "SONNET",
    "STANZA", "METAPHOR", "ALLEGORY", "PROTAGONIST", "VILLAIN", "CLIMAX", "PLOT",
    "GENRE", "PREFACE", "ANTHOLOGY", "PAPERBACK", "HARDCOVER", "LIBRARY",
    "MANUSCRIPT", "BIOGRAPHY", "ODYSSEY", "ULYSSES", "HAMLET", "MACBETH",
    "GATSBY", "DRACULA", "FRANKENSTEIN", "MATILDA", "HOBBIT", "NARNIA",
    "ORWELL", "AUSTEN", "TOLKIEN", "DICKENS", "HEMINGWAY", "ROWLING", "TWAIN",
    "BRONTE", "KAFKA", "HOMER", "DANTE", "SHAKESPEARE", "STEINBECK", "ATWOOD",
    "BRADBURY", "HUXLEY", "WILDE", "VERSE", "RHYME", "TRAGEDY", "COMEDY",
    "PARABLE", "FOLKLORE", "LEGEND", "EPIC", "HAIKU", "PARCHMENT", "INKWELL",
]


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _mask(word: str, guessed: list) -> str:
    return " ".join(c if c in guessed else "▢" for c in word)


def _reward(wrong: int) -> float:
    return round(max(0.1, 0.5 - 0.07 * wrong), 2)


def _board(word: str, guessed: list, wrong: int, status: str) -> tuple[str, list]:
    lives = _MAX_WRONG - wrong
    hearts = "❤️" * lives + "🖤" * wrong
    wrong_letters = [g for g in guessed if g not in word]
    head = (f"🔤 <b>Literary Hangman</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"{hearts}\n\n<code>{_mask(word, guessed)}</code>\n")
    if wrong_letters:
        head += f"\n❌ Missed: {' '.join(wrong_letters)}\n"

    rows = []
    if status == "active":
        remaining = [c for c in _ALPHA if c not in guessed]
        for i in range(0, len(remaining), 7):
            rows.append([btn(c, f"hm:{c}", style="primary") for c in remaining[i:i + 7]])
        head += "\n👇 Pick a letter:"
    else:
        won = status == "won"
        rwd = _reward(wrong) if won else 0.0
        head += (f"\n🎉 <b>Solved it!</b> The word was <b>{word}</b>.\n💎 <b>+{fmt_amount(rwd)} BGM</b>"
                 if won else f"\n💀 <b>Out of guesses!</b> The word was <b>{word}</b>.")
        rows.append([btn("🔁 New Word", "hm_new", style="success"),
                     btn("🎮 Games", "menu_games", style="primary")])
    return head, rows


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"hm_day": 1, "hm_plays": 1}) or {}
    return int(u.get("hm_plays") or 0) if u.get("hm_day") == _today() else 0


async def _start(message: Message, uid: int, *, edit: bool) -> None:
    from utils.flags import is_on
    if not await is_on("games"):
        await (message.edit_text if edit else message.answer)(
            "🎮 <b>Games are paused</b> right now — check back soon!",
            reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    if await _plays_today(db, uid) >= _DAILY:
        txt = f"🔤 <b>Hangman</b>\n\nDaily limit reached ({_DAILY}/day). Come back tomorrow!"
        mk = kb([btn("🎮 Games", "menu_games", style="primary")])
        await (message.edit_text if edit else message.answer)(txt, reply_markup=mk)
        return
    # count this play
    today = _today()
    prev = await _plays_today(db, uid)
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"hm_day": today, "hm_plays": prev + 1}})
    word = random.choice(_WORDS)
    await db.safe_update("hangman_games", {"uid": uid},
                         {"$set": {"uid": uid, "word": word, "guessed": [], "wrong": 0,
                                   "status": "active", "started_at": _now()}})
    text, rows = _board(word, [], 0, "active")
    await (message.edit_text if edit else message.answer)(text, reply_markup=kb(*rows))


@router.message(Command("hangman"))
async def cmd_hangman(message: Message) -> None:
    await _start(message, message.chat.id, edit=False)


@router.callback_query(F.data == "menu_hangman")
async def cb_open(call: CallbackQuery) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, edit=True)


@router.callback_query(F.data == "hm_new")
async def cb_new(call: CallbackQuery) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, edit=True)


@router.callback_query(F.data.startswith("hm:"))
async def cb_guess(call: CallbackQuery) -> None:
    letter = call.data.split(":", 1)[1]
    uid = call.from_user.id
    db = await MongoManager.get()
    g = await db.find_one_global("hangman_games", {"uid": uid})
    if not g or g.get("status") != "active":
        await call.answer("Start a new game.", show_alert=True)
        return
    if letter not in _ALPHA or letter in (g.get("guessed") or []):
        await call.answer("Already tried that.")
        return

    word = g["word"]
    guessed = list(g.get("guessed") or []) + [letter]
    wrong = sum(1 for x in guessed if x not in word)
    if all(c in guessed for c in word):
        status = "won"
    elif wrong >= _MAX_WRONG:
        status = "lost"
    else:
        status = "active"

    await call.answer("✅" if letter in word else "❌")
    if status == "active":
        await db.safe_update("hangman_games", {"uid": uid},
                             {"$set": {"guessed": guessed, "wrong": wrong, "status": "active"}},
                             upsert=False)
    else:
        # Finalize atomically (active → won/lost) so a double-tap on the last
        # letters can't credit the win twice.
        claimed = await db.find_one_and_update_global(
            "hangman_games", {"uid": uid, "status": "active"},
            {"$set": {"guessed": guessed, "wrong": wrong, "status": status}})
        if claimed and status == "won":
            rwd = _reward(wrong)
            await add_bgm(uid, rwd)
            await db.safe_update("users", {"user_id": uid},
                                 {"$inc": {"games_played": 1, "game_bgm": rwd}})
            from utils.missions import mark
            await mark(uid, "play_game")
    text, rows = _board(word, guessed, wrong, status)
    await call.message.edit_text(text, reply_markup=kb(*rows))
