"""
utils/games.py — Quiz & True/False engine (server-authoritative).

Security model: correct answers are NEVER sent to the client. The client only
gets question text + options. Scoring, daily limits, and token credits all
happen here, server-side, keyed to the Telegram-verified user id. Sessions are
single-use so a winning submission can't be replayed.

Spec (locked):
  Quiz: 8 Q, 2/day, 15-min limit. correct/wrong by level
        beginner +0.0625/-0.03125, moderate +0.09375/-0.046875,
        advanced +0.125/-0.0625. Skip = 0.1 (BCN-first). Speed bonus +0.5 BGM
        if all 8 done < 2 min (once/day).
  TF:   20 Q, 2/day, 15-min limit (timeout -0.1 BGM). correct +0.05, wrong
        -0.01, skip 0.01 (BCN-first). No bonus.
"""
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from database.connection import MongoManager
from utils.wallet import add_bgm, get_balances, spend

QUIZ_REWARD = {
    "beginner": (0.0625, 0.03125),
    "moderate": (0.09375, 0.046875),
    "advanced": (0.125, 0.0625),
}

CONFIG = {
    "quiz": {"count": 8, "daily": 2, "time_limit": 900, "skip_cost": 0.1,
             "speed_bonus": 0.5, "speed_secs": 120, "levels": True},
    "tf":   {"count": 20, "daily": 2, "time_limit": 900, "skip_cost": 0.01,
             "correct": 0.05, "wrong": 0.01, "timeout_penalty": 0.1, "levels": False},
}


def _now():
    return datetime.now(timezone.utc)


def _sid() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


# ── seed a small starter bank so games work out of the box ──────────────────────
_SEED_QUIZ = [
    ("beginner", "Who wrote 'Romeo and Juliet'?",
     {"A": "Dickens", "B": "Shakespeare", "C": "Tolstoy", "D": "Austen"}, "B"),
    ("beginner", "'1984' was written by?",
     {"A": "Orwell", "B": "Huxley", "C": "Bradbury", "D": "Wells"}, "A"),
    ("beginner", "The Harry Potter series is by?",
     {"A": "Rowling", "B": "Tolkien", "C": "Lewis", "D": "Pullman"}, "A"),
    ("beginner", "'The Odyssey' is attributed to?",
     {"A": "Virgil", "B": "Homer", "C": "Plato", "D": "Ovid"}, "B"),
    ("beginner", "Sherlock Holmes was created by?",
     {"A": "Christie", "B": "Poe", "C": "Doyle", "D": "Chesterton"}, "C"),
    ("beginner", "'Pride and Prejudice' author?",
     {"A": "Austen", "B": "Bronte", "C": "Eliot", "D": "Gaskell"}, "A"),
    ("beginner", "'The Great Gatsby' author?",
     {"A": "Hemingway", "B": "Fitzgerald", "C": "Steinbeck", "D": "Faulkner"}, "B"),
    ("beginner", "'Moby-Dick' author?",
     {"A": "Melville", "B": "Twain", "C": "Hawthorne", "D": "Cooper"}, "A"),
    ("moderate", "'Crime and Punishment' author?",
     {"A": "Tolstoy", "B": "Chekhov", "C": "Dostoevsky", "D": "Gogol"}, "C"),
    ("moderate", "'One Hundred Years of Solitude' author?",
     {"A": "Borges", "B": "Marquez", "C": "Llosa", "D": "Cortazar"}, "B"),
    ("moderate", "'Brave New World' author?",
     {"A": "Orwell", "B": "Huxley", "C": "Atwood", "D": "Burgess"}, "B"),
    ("moderate", "'The Brothers Karamazov' author?",
     {"A": "Dostoevsky", "B": "Tolstoy", "C": "Turgenev", "D": "Pushkin"}, "A"),
    ("advanced", "'Ulysses' author?",
     {"A": "Beckett", "B": "Joyce", "C": "Yeats", "D": "Wilde"}, "B"),
    ("advanced", "'In Search of Lost Time' author?",
     {"A": "Proust", "B": "Camus", "C": "Sartre", "D": "Flaubert"}, "A"),
    ("advanced", "'Gravity's Rainbow' author?",
     {"A": "DeLillo", "B": "Pynchon", "C": "Roth", "D": "Updike"}, "B"),
    ("advanced", "'The Sound and the Fury' author?",
     {"A": "Faulkner", "B": "Hemingway", "C": "Wolfe", "D": "Dos Passos"}, "A"),
]
_SEED_TF = [
    ("'War and Peace' was written by Leo Tolstoy.", True),
    ("George Orwell wrote 'Animal Farm'.", True),
    ("'Frankenstein' was written by Bram Stoker.", False),
    ("Jane Austen wrote 'Wuthering Heights'.", False),
    ("'The Hobbit' precedes 'The Lord of the Rings'.", True),
    ("Mark Twain's real name was Samuel Clemens.", True),
    ("'Don Quixote' was written by Cervantes.", True),
    ("Agatha Christie created Hercule Poirot.", True),
    ("'Dracula' was written by Mary Shelley.", False),
    ("'The Catcher in the Rye' is by J.D. Salinger.", True),
    ("Homer wrote 'The Divine Comedy'.", False),
    ("'Hamlet' is a comedy.", False),
]


async def ensure_seed() -> None:
    db = await MongoManager.get()
    if await db.count_global("questions", {"game": "quiz"}) == 0:
        for level, q, opts, a in _SEED_QUIZ:
            await db.safe_insert("questions", {"game": "quiz", "level": level,
                                               "q": q, "options": opts, "a": a})
    if await db.count_global("questions", {"game": "tf"}) == 0:
        for q, a in _SEED_TF:
            await db.safe_insert("questions", {"game": "tf", "q": q, "a": bool(a)})


# ── daily limit ──────────────────────────────────────────────────────────────
async def plays_today(uid: int, game: str) -> int:
    db = await MongoManager.get()
    since = _now() - timedelta(hours=24)
    return await db.count_global("game_sessions",
                                 {"uid": uid, "game": game, "started_at": {"$gte": since}})


# ── new session ──────────────────────────────────────────────────────────────
async def new_session(uid: int, game: str, level: str = "beginner") -> dict:
    cfg = CONFIG[game]
    if await plays_today(uid, game) >= cfg["daily"]:
        return {"error": f"Daily limit reached ({cfg['daily']}/day)."}

    db = await MongoManager.get()
    flt: dict[str, Any] = {"game": game}
    if cfg["levels"]:
        flt["level"] = level
    pool = await db.find_global("questions", flt)
    if len(pool) < 1:
        return {"error": "No questions available yet."}

    random.shuffle(pool)
    chosen = pool[:cfg["count"]]
    sid = _sid()
    # store correct answers server-side; client never sees them
    answers = [{"a": q.get("a")} for q in chosen]
    await db.safe_insert("game_sessions", {
        "session_id": sid, "uid": uid, "game": game, "level": level,
        "answers": answers, "started_at": _now(), "status": "active",
    })

    public_q = []
    for q in chosen:
        item = {"q": q.get("q")}
        if game == "quiz":
            item["options"] = q.get("options")
        public_q.append(item)

    payload = {"session_id": sid, "game": game, "level": level,
               "questions": public_q, "time_limit": cfg["time_limit"]}
    if game == "quiz":
        rwd, pen = QUIZ_REWARD[level]
        payload.update({"reward": rwd, "penalty": pen, "skip_cost": cfg["skip_cost"],
                        "speed_bonus": cfg["speed_bonus"], "speed_secs": cfg["speed_secs"]})
    else:
        payload.update({"reward": cfg["correct"], "penalty": cfg["wrong"],
                        "skip_cost": cfg["skip_cost"]})
    return payload


# ── submit & score (authoritative) ─────────────────────────────────────────────
async def submit(uid: int, session_id: str, client_answers: list) -> dict:
    db = await MongoManager.get()
    sess = await db.find_one_global("game_sessions", {"session_id": session_id})
    if not sess or sess.get("uid") != uid:
        return {"error": "Invalid session."}
    if sess.get("status") != "active":
        return {"error": "This session is already finished."}

    game = sess["game"]
    cfg = CONFIG[game]
    correct_list = sess["answers"]
    elapsed = (_now() - sess["started_at"]).total_seconds()
    timed_out = elapsed > cfg["time_limit"]

    # mark done immediately (single-use → no replay)
    await db.safe_update("game_sessions", {"session_id": session_id},
                         {"$set": {"status": "done", "finished_at": _now()}}, upsert=False)

    n = len(correct_list)
    answers = (client_answers or [])[:n]
    correct = wrong = skipped = 0

    if game == "quiz":
        rwd, pen = QUIZ_REWARD[sess["level"]]
        for i in range(n):
            given = answers[i] if i < len(answers) else None
            truth = correct_list[i]["a"]
            if given is None or given == "":
                skipped += 1
            elif str(given).upper() == str(truth).upper():
                correct += 1
            else:
                wrong += 1
    else:  # tf
        rwd, pen = cfg["correct"], cfg["wrong"]
        for i in range(n):
            given = answers[i] if i < len(answers) else None
            truth = bool(correct_list[i]["a"])
            if given is None:
                skipped += 1
            elif bool(given) == truth:
                correct += 1
            else:
                wrong += 1

    # token math ----------------------------------------------------------------
    net = round(correct * rwd - wrong * pen, 5)
    # skips are BCN-first spends
    skip_cost = cfg["skip_cost"]
    skips_charged = 0
    for _ in range(skipped):
        if await spend(uid, skip_cost):
            skips_charged += 1
        # if they can't pay, the skip is free (couldn't have skipped in UI anyway)

    bonus = 0.0
    if game == "quiz" and not timed_out and skipped == 0 and wrong == 0 and correct == n:
        # full clear under time → eligible for speed bonus (once/day)
        if elapsed <= cfg["speed_secs"]:
            today = _now().strftime("%Y-%m-%d")
            u = await db.find_one_global("users", {"user_id": uid},
                                         {"speed_bonus_day": 1}) or {}
            if u.get("speed_bonus_day") != today:
                bonus = cfg["speed_bonus"]
                await db.safe_update("users", {"user_id": uid},
                                     {"$set": {"speed_bonus_day": today}})

    tf_timeout_penalty = 0.0
    if game == "tf" and timed_out:
        tf_timeout_penalty = cfg["timeout_penalty"]

    total_delta = round(net + bonus - tf_timeout_penalty, 5)
    if total_delta >= 0:
        await add_bgm(uid, total_delta)
    else:
        # clamp so balance can't go below zero
        bgm, _ = await get_balances(uid)
        await add_bgm(uid, -min(abs(total_delta), bgm))

    return {
        "ok": True, "correct": correct, "wrong": wrong, "skipped": skipped,
        "net_bgm": round(net, 4), "speed_bonus": bonus,
        "timeout_penalty": tf_timeout_penalty, "total": total_delta,
        "timed_out": timed_out,
    }
