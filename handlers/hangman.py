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
from utils.games import daily_limit
from utils.keyboards import btn, kb
from utils.premium import is_premium
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()

_ALPHA = string.ascii_uppercase
_MAX_WRONG = 6

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
    head = (f"🎮 <b>Literary Hangman</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>One hidden word from the world of books — reveal it letter by letter.</i>\n"
            f"<blockquote>{hearts}\n<i>Guesses remaining:</i> <code>{lives}/{_MAX_WRONG}</code>\n\n"
            f"<code>{_mask(word, guessed)}</code></blockquote>")
    if wrong_letters:
        head += f"\n❌ <b>Missed letters:</b> <code>{' '.join(wrong_letters)}</code>"

    rows = []
    if status == "active":
        remaining = [c for c in _ALPHA if c not in guessed]
        for i in range(0, len(remaining), 7):
            rows.append([btn(c, f"hm:{c}", style="primary") for c in remaining[i:i + 7]])
        head += "\n\n👇 <i>Tap a letter to make your guess.</i>"
    else:
        won = status == "won"
        rwd = _reward(wrong) if won else 0.0
        head += (f"\n\n✨ <b>Solved it — beautifully done!</b>\n"
                 f"<blockquote>The word was <b>{word}</b>.\n"
                 f"🎁 <i>Reward credited:</i> 💎 <b>+{fmt_amount(rwd)} BGM</b> — straight to your wallet.</blockquote>\n"
                 f"<i>💡 Fewer misses earn a richer reward. Care for another?</i>"
                 if won else
                 f"\n\n💀 <b>Out of guesses — that one held out.</b>\n"
                 f"<blockquote>The word was <b>{word}</b>.\n"
                 f"<i>No streak lost — every round sharpens your eye for the next.</i></blockquote>\n"
                 f"<i>💡 Line up a fresh word and reclaim the win.</i>")
        rows.append([btn("🔁 New Word", "hm_new", style="success"),
                     btn("🎮 Games Hub", "menu_games", style="primary")])
    return head, rows


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"hm_day": 1, "hm_plays": 1}) or {}
    return int(u.get("hm_plays") or 0) if u.get("hm_day") == _today() else 0


async def _start(message: Message, uid: int, *, edit: bool) -> None:
    from utils.flags import is_on
    if not await is_on("games"):
        await (message.edit_text if edit else message.answer)(
            "⏳ <b>Games are taking a short break</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Our game room is being polished right now. It'll be back shortly — "
            "your library and rewards are untouched in the meantime.</blockquote>\n"
            "<i>💡 Check back soon — there's BGM waiting to be won.</i>",
            reply_markup=kb([btn("🔙 Back to Menu", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    lim = await daily_limit(uid)
    if await _plays_today(db, uid) >= lim:
        free = not await is_premium(uid)
        txt = (f"🎮 <b>Literary Hangman</b>\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"⏳ <b>You've played today's full set.</b>\n"
               f"<blockquote>You've used all <code>{lim}</code> rounds for today — nicely done. "
               f"Your guessing board resets at midnight, ready for a clean run.\n\n"
               f"<i>Meanwhile, plenty more to win across the Games Hub.</i></blockquote>\n"
               f"<i>💡 Come back tomorrow for a fresh streak of rounds.</i>")
        rows = []
        if free:
            txt += "\n<i>👑 Premium plays 5 rounds a day, every game.</i>"
            rows.append([btn("👑 Go Premium for 5/day", "go_premium", style="success")])
        rows.append([btn("🎮 Games Hub", "menu_games", style="primary")])
        await (message.edit_text if edit else message.answer)(txt, reply_markup=kb(*rows))
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
        await call.answer("This round has wrapped up. Tap New Word to start a fresh one.", show_alert=True)
        return
    if letter not in _ALPHA or letter in (g.get("guessed") or []):
        await call.answer("You've already tried that letter — pick a new one.")
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

    await call.answer("✅ Nice — that letter's in the word!" if letter in word
                      else "❌ Not in this one — choose another.")
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
