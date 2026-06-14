"""
utils/bookle.py — "Bookle": a Wordle-style daily word game (book-themed).

Server-authoritative: the secret word never leaves the server until the game
ends; guesses are validated and scored here, one game per user per day (everyone
gets the SAME daily word — social/shareable). Win reward scales with how few
tries it took, credited once.
"""
import random
from datetime import datetime, timezone

from database.connection import MongoManager
from utils.wallet import add_bgm

# Five-letter, book/reading-themed answers.
WORDS = [
    "NOVEL", "PROSE", "VERSE", "TOMES", "PAGES", "GENRE", "PLOTS", "DRAFT",
    "QUILL", "SCENE", "ESSAY", "FABLE", "LYRIC", "STORY", "COVER", "PRINT",
    "EPICS", "RHYME", "DRAMA", "CANON", "INDEX", "READS", "WORDS", "BOUND",
    "REALM", "MYTHS", "ODYSE", "TROPE", "BLURB", "SPINE",
]
LEN = 5
MAX_TRIES = 6
_REWARDS = [0.60, 0.45, 0.35, 0.25, 0.18, 0.10]  # by try-number (1..6)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _day_index() -> int:
    return (datetime.now(timezone.utc).date() - datetime(2020, 1, 1, tzinfo=timezone.utc).date()).days


def daily_word() -> str:
    return WORDS[_day_index() % len(WORDS)]


def _feedback(guess: str, word: str) -> str:
    """Wordle marks: c=correct spot, p=present elsewhere, a=absent (dup-safe)."""
    fb = ["a"] * LEN
    pool = list(word)
    for i in range(LEN):
        if guess[i] == word[i]:
            fb[i] = "c"
            pool[i] = None
    for i in range(LEN):
        if fb[i] == "c":
            continue
        if guess[i] in pool:
            fb[i] = "p"
            pool[pool.index(guess[i])] = None
    return "".join(fb)


def _public(sess: dict) -> dict:
    done = sess.get("status") in ("won", "lost")
    out = {"length": LEN, "max": MAX_TRIES, "guesses": sess.get("guesses", []),
           "status": sess.get("status", "active")}
    if done:
        out["word"] = sess.get("word")
        out["reward"] = sess.get("reward", 0)
    return out


async def get_or_create(uid: int) -> dict:
    db = await MongoManager.get()
    day = _today()
    sess = await db.find_one_global("bookle_sessions", {"uid": uid, "day": day})
    if not sess:
        sess = {"uid": uid, "day": day, "word": daily_word(), "guesses": [],
                "status": "active", "reward": 0, "created_at": datetime.now(timezone.utc)}
        await db.safe_insert("bookle_sessions", sess)
    return _public(sess)


async def guess(uid: int, raw: str) -> dict:
    g = (raw or "").strip().upper()
    if len(g) != LEN or not g.isalpha():
        return {"error": "Enter a 5-letter word."}
    db = await MongoManager.get()
    day = _today()
    sess = await db.find_one_global("bookle_sessions", {"uid": uid, "day": day})
    if not sess:
        return await get_or_create(uid)
    if sess.get("status") != "active":
        return _public(sess)
    guesses = sess.get("guesses", [])
    if len(guesses) >= MAX_TRIES:
        return _public(sess)

    word = sess["word"]
    fb = _feedback(g, word)
    guesses.append({"g": g, "fb": fb})
    update = {"guesses": guesses}
    reward = 0.0
    if g == word:
        update["status"] = "won"
        reward = _REWARDS[len(guesses) - 1]
        update["reward"] = reward
    elif len(guesses) >= MAX_TRIES:
        update["status"] = "lost"

    await db.safe_update("bookle_sessions", {"uid": uid, "day": day}, {"$set": update}, upsert=False)

    if reward > 0:
        await add_bgm(uid, reward)
        await db.safe_update("users", {"user_id": uid},
                             {"$inc": {"games_played": 1, "game_bgm": reward}})

    sess.update(update)
    return _public(sess)
