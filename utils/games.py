"""
utils/games.py — Quiz / True-False / book-MCQ engine (server-authoritative).

Security model: correct answers are NEVER sent to the client. The client gets
question text + options only. Scoring, daily limits and token credits all happen
here, server-side, keyed to the Telegram-verified user id. Sessions are
single-use so a winning submission can't be replayed.

Player experience (2026 overhaul):
  • ONE 15-minute clock for the WHOLE session (not per question).
  • Free navigation — jump to any question, leave some blank, come back later.
    Skipping costs NOTHING; an unanswered question is simply neutral.
  • A per-user ROTATION cursor (game_progress) serves fresh questions every play
    and only repeats once the whole bank has been cycled — so a big bank feels
    endless.
  • submit() returns a performance grade/tag, XP and a full answer review so the
    Mini App can show a proper gaming result screen (wins up front, penalties
    kept small).

Economy (kept from the locked spec, minus the skip tax):
  Quiz: 8 Q, 2/day. correct/wrong by level
        beginner +0.0625/-0.03125, moderate +0.09375/-0.046875,
        advanced +0.125/-0.0625. Speed bonus +0.5 BGM if a full clear < 2 min
        (once/day).
  TF:   20 Q, 2/day. correct +0.05, wrong -0.01.
  Book games (guess / firstline / author): 6 Q, 3/day, fixed rewards.
"""
import hashlib
import json
import os
import random
import re
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo.errors import BulkWriteError

from database.connection import MongoManager
from utils.wallet import add_bgm, cut_bgm, get_balances

QUIZ_REWARD = {
    "beginner": (0.0625, 0.03125),
    "moderate": (0.09375, 0.046875),
    "advanced": (0.125, 0.0625),
}

# Note: no "skip_cost" — navigating past a question is free now.
CONFIG = {
    "quiz": {"count": 8, "daily": 2, "time_limit": 900,
             "speed_bonus": 0.5, "speed_secs": 120, "levels": True, "mcq": True},
    "tf":   {"count": 20, "daily": 2, "time_limit": 900,
             "correct": 0.05, "wrong": 0.01, "levels": False, "mcq": False},
    "guess":     {"count": 6, "daily": 3, "time_limit": 900,
                  "correct": 0.08, "wrong": 0.04, "levels": False, "mcq": True},
    "firstline": {"count": 6, "daily": 3, "time_limit": 900,
                  "correct": 0.08, "wrong": 0.04, "levels": False, "mcq": True},
    "author":    {"count": 6, "daily": 3, "time_limit": 900,
                  "correct": 0.07, "wrong": 0.035, "levels": False, "mcq": True},
}

VALID_GAMES = tuple(CONFIG.keys())


def _now():
    return datetime.now(timezone.utc)


def _sid() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def qhash(game: str, q: str) -> str:
    """Stable dedupe key: normalise whitespace/case and namespace by game."""
    norm = re.sub(r"\s+", " ", (q or "").strip().lower())
    return hashlib.sha1(f"{game}|{norm}".encode("utf-8")).hexdigest()


def _grade(accuracy: float) -> tuple[str, str]:
    """Map accuracy (0..1) to a (key, friendly message) performance tag."""
    if accuracy >= 0.9:
        return "legendary", "Legendary run — a near-flawless performance. Your shelf salutes you."
    if accuracy >= 0.75:
        return "great", "Brilliantly played — that was a genuinely sharp round."
    if accuracy >= 0.5:
        return "good", "Solid run — more right than wrong, and the rewards prove it."
    if accuracy >= 0.25:
        return "meh", "A respectable effort — a little more practice and the top scores are yours."
    return "low", "Every reader starts here — play again and watch your score climb."


# ── starter bank (tiny inline fallback so games work on a brand-new DB) ──────────
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
_SEED_GUESS = [
    ("A young wizard discovers he's famous and attends a school of magic.",
     {"A": "Harry Potter", "B": "The Hobbit", "C": "Eragon", "D": "Percy Jackson"}, "A"),
    ("A dystopia where a totalitarian state watches everyone via telescreens.",
     {"A": "Brave New World", "B": "1984", "C": "Fahrenheit 451", "D": "We"}, "B"),
    ("A girl falls down a rabbit hole into a nonsensical wonderland.",
     {"A": "Peter Pan", "B": "The Wizard of Oz", "C": "Alice in Wonderland", "D": "Coraline"}, "C"),
    ("An obsessive captain hunts a giant white whale across the seas.",
     {"A": "Treasure Island", "B": "Moby-Dick", "C": "The Old Man and the Sea", "D": "20,000 Leagues"}, "B"),
    ("A wealthy man throws lavish parties pining for a lost love across the bay.",
     {"A": "The Great Gatsby", "B": "Wuthering Heights", "C": "Atonement", "D": "Rebecca"}, "A"),
    ("Four siblings enter a magical land through a wardrobe.",
     {"A": "The Golden Compass", "B": "Narnia", "C": "Inkheart", "D": "Stardust"}, "B"),
]
_SEED_FIRSTLINE = [
    ("\"Call me Ishmael.\"",
     {"A": "Moby-Dick", "B": "Dracula", "C": "Frankenstein", "D": "Robinson Crusoe"}, "A"),
    ("\"It was the best of times, it was the worst of times.\"",
     {"A": "Great Expectations", "B": "A Tale of Two Cities", "C": "Oliver Twist", "D": "Hard Times"}, "B"),
    ("\"It is a truth universally acknowledged, that a single man... must be in want of a wife.\"",
     {"A": "Emma", "B": "Jane Eyre", "C": "Pride and Prejudice", "D": "Middlemarch"}, "C"),
    ("\"All happy families are alike; each unhappy family is unhappy in its own way.\"",
     {"A": "War and Peace", "B": "Anna Karenina", "C": "Doctor Zhivago", "D": "Fathers and Sons"}, "B"),
    ("\"It was a bright cold day in April, and the clocks were striking thirteen.\"",
     {"A": "1984", "B": "Animal Farm", "C": "Brave New World", "D": "Fahrenheit 451"}, "A"),
    ("\"In a hole in the ground there lived a hobbit.\"",
     {"A": "The Silmarillion", "B": "The Hobbit", "C": "The Fellowship of the Ring", "D": "Eragon"}, "B"),
]
_SEED_AUTHOR = [
    ("Who wrote 'The Old Man and the Sea'?",
     {"A": "Steinbeck", "B": "Hemingway", "C": "Faulkner", "D": "Fitzgerald"}, "B"),
    ("Who wrote 'Beloved'?",
     {"A": "Toni Morrison", "B": "Alice Walker", "C": "Maya Angelou", "D": "Zora Neale Hurston"}, "A"),
    ("Who wrote 'The Name of the Rose'?",
     {"A": "Calvino", "B": "Eco", "C": "Saramago", "D": "Borges"}, "B"),
    ("Who wrote 'Norwegian Wood'?",
     {"A": "Murakami", "B": "Ishiguro", "C": "Mishima", "D": "Kawabata"}, "A"),
    ("Who wrote 'The Handmaid's Tale'?",
     {"A": "Le Guin", "B": "Atwood", "C": "Butler", "D": "Jemisin"}, "B"),
    ("Who wrote 'Things Fall Apart'?",
     {"A": "Achebe", "B": "Soyinka", "C": "Ngugi", "D": "Adichie"}, "A"),
]


def _seed_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "questions_seed.json")


def _normalize_seed(doc: dict) -> Optional[dict]:
    """Coerce a raw seed dict into a storable question doc (+ qhash), or None."""
    game = doc.get("game")
    q = (doc.get("q") or "").strip()
    if game not in VALID_GAMES or not q:
        return None
    out: dict[str, Any] = {"game": game, "q": q, "qhash": qhash(game, q)}
    if game == "tf":
        out["a"] = bool(doc.get("a"))
    else:
        opts = doc.get("options") or {}
        if not all(opts.get(k) for k in ("A", "B", "C", "D")):
            return None
        ans = str(doc.get("a", "")).upper()
        if ans not in ("A", "B", "C", "D"):
            return None
        out["options"] = {k: str(opts[k]) for k in ("A", "B", "C", "D")}
        out["a"] = ans
    if game == "quiz":
        lvl = doc.get("level", "beginner")
        out["level"] = lvl if lvl in QUIZ_REWARD else "beginner"
    return out


async def _bulk_load_seed_file(db) -> int:
    """Fast-load the shipped seed bank on a fresh DB. De-dupes on the unique
    qhash index (ordered=False → duplicates are skipped, not fatal)."""
    path = _seed_path()
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return 0
    docs, seen = [], set()
    for d in raw if isinstance(raw, list) else []:
        norm = _normalize_seed(d)
        if not norm or norm["qhash"] in seen:
            continue
        seen.add(norm["qhash"])
        docs.append(norm)
    if not docs:
        return 0
    coll = db.dbs[db.write_idx]["questions"]
    loaded = 0
    for i in range(0, len(docs), 1000):
        chunk = docs[i:i + 1000]
        try:
            res = await coll.insert_many(chunk, ordered=False)
            loaded += len(res.inserted_ids)
        except BulkWriteError as bwe:
            loaded += bwe.details.get("nInserted", 0)
        except Exception:
            pass
    return loaded


async def _insert_q(db, doc: dict) -> None:
    norm = _normalize_seed(doc)
    if norm:
        await db.safe_insert("questions", norm)


async def ensure_seed() -> None:
    """Seed the question bank on first boot. Prefers the large shipped seed file
    (data/questions_seed.json); falls back to the tiny inline bank per game so
    every game is always playable even without the file."""
    db = await MongoManager.get()
    # Only attempt the big file-load when the bank is essentially empty — avoids
    # re-scanning thousands of rows on every restart.
    if await db.count_global("questions") < 50:
        await _bulk_load_seed_file(db)
    # Per-game inline fallback (covers any game the file didn't include).
    if await db.count_global("questions", {"game": "quiz"}) == 0:
        for level, q, opts, a in _SEED_QUIZ:
            await _insert_q(db, {"game": "quiz", "level": level, "q": q, "options": opts, "a": a})
    if await db.count_global("questions", {"game": "tf"}) == 0:
        for q, a in _SEED_TF:
            await _insert_q(db, {"game": "tf", "q": q, "a": bool(a)})
    for gtype, seed in (("guess", _SEED_GUESS), ("firstline", _SEED_FIRSTLINE),
                        ("author", _SEED_AUTHOR)):
        if await db.count_global("questions", {"game": gtype}) == 0:
            for q, opts, a in seed:
                await _insert_q(db, {"game": gtype, "q": q, "options": opts, "a": a})


# ── daily limit ──────────────────────────────────────────────────────────────
async def daily_limit(uid: int) -> int:
    """Tier-aware per-game daily cap: FREE q_game_free (2), PREMIUM q_game_premium (5).
    Shared by the Mini-App engine and every chat game so the limit lives in one place."""
    from utils.premium import is_premium
    from utils.settings import get_float
    return int(await get_float("q_game_premium" if await is_premium(uid) else "q_game_free"))


async def plays_today(uid: int, game: str) -> int:
    db = await MongoManager.get()
    since = _now() - timedelta(hours=24)
    return await db.count_global("game_sessions",
                                 {"uid": uid, "game": game, "started_at": {"$gte": since}})


# ── new session (with per-user rotation) ────────────────────────────────────────
async def new_session(uid: int, game: str, level: str = "beginner") -> dict:
    cfg = CONFIG[game]
    db = await MongoManager.get()
    lim = await daily_limit(uid)
    if await plays_today(uid, game) >= lim:
        return {"error": f"You've enjoyed all {lim} plays for today — nicely done. Your rounds refresh in the morning, so come back tomorrow to keep earning."}

    if cfg["levels"]:
        if level not in QUIZ_REWARD:
            level = "beginner"
    else:
        level = "all"   # rotation/progress key for level-less games

    base: dict[str, Any] = {"game": game}
    if cfg["levels"]:
        base["level"] = level

    # rotation: serve questions this user hasn't seen this cycle first
    prog = await db.find_one_global("game_progress",
                                    {"uid": uid, "game": game, "level": level}) or {}
    served = prog.get("served", [])
    served_oids = []
    for s in served:
        try:
            served_oids.append(ObjectId(s))
        except Exception:  # noqa: BLE001 — ignore malformed cursor entries
            pass

    match = dict(base)
    if served_oids:
        match["_id"] = {"$nin": served_oids}
    chosen = await db.sample_global("questions", match, cfg["count"])

    reset = False
    if len(chosen) < cfg["count"]:
        # this user has exhausted the bank for this game/level → new cycle
        chosen = await db.sample_global("questions", base, cfg["count"])
        reset = True
    if not chosen:
        return {"error": "We're curating fresh questions for this game right now — the library is being stocked as we speak. Please check back in a little while."}

    chosen_ids = [str(q["_id"]) for q in chosen]
    new_served = chosen_ids if reset else (served + chosen_ids)
    await db.safe_update("game_progress", {"uid": uid, "game": game, "level": level},
                         {"$set": {"served": new_served, "updated_at": _now()}}, upsert=True)

    sid = _sid()
    # full questions (incl. answers + options) stored server-side for scoring/review
    qs = [{"q": q.get("q"), "options": q.get("options"), "a": q.get("a")} for q in chosen]
    await db.safe_insert("game_sessions", {
        "session_id": sid, "uid": uid, "game": game, "level": level,
        "qs": qs, "started_at": _now(), "status": "active",
    })

    public_q = []
    for q in chosen:
        item = {"q": q.get("q")}
        if cfg.get("mcq"):
            item["options"] = q.get("options")
        public_q.append(item)

    payload = {
        "session_id": sid, "game": game,
        "level": level if cfg["levels"] else None,
        "questions": public_q, "count": len(public_q),
        "time_limit": cfg["time_limit"], "mcq": bool(cfg.get("mcq")),
    }
    if game == "quiz":
        rwd, _pen = QUIZ_REWARD[level]
        payload.update({"reward": rwd, "speed_bonus": cfg["speed_bonus"],
                        "speed_secs": cfg["speed_secs"]})
    else:
        payload.update({"reward": cfg["correct"]})
    return payload


# ── submit & score (authoritative) ─────────────────────────────────────────────
async def submit(uid: int, session_id: str, client_answers: list) -> dict:
    db = await MongoManager.get()
    sess = await db.find_one_global("game_sessions", {"session_id": session_id})
    if not sess or sess.get("uid") != uid:
        return {"error": "We couldn't find this game session — it may have expired. Head back and start a fresh round to keep playing."}
    if sess.get("status") != "active":
        return {"error": "This round has already been scored and your rewards are safely banked. Start a new game whenever you're ready for the next one."}

    game = sess["game"]
    cfg = CONFIG[game]
    qs = sess.get("qs") or []
    elapsed = (_now() - sess["started_at"]).total_seconds()
    timed_out = elapsed > cfg["time_limit"] + 5  # small grace for the round-trip

    # mark done immediately (single-use → no replay)
    await db.safe_update("game_sessions", {"session_id": session_id},
                         {"$set": {"status": "done", "finished_at": _now()}}, upsert=False)

    n = len(qs)
    answers = (client_answers or [])[:n]
    is_mcq = cfg.get("mcq")
    rwd, pen = (QUIZ_REWARD[sess.get("level", "beginner")] if game == "quiz"
                else (cfg["correct"], cfg["wrong"]))

    correct = wrong = skipped = 0
    review = []
    for i in range(n):
        given = answers[i] if i < len(answers) else None
        truth = qs[i].get("a")
        ok = False
        unanswered = False
        if is_mcq:
            if given is None or given == "":
                skipped += 1
                unanswered = True
            elif str(given).upper() == str(truth).upper():
                correct += 1
                ok = True
            else:
                wrong += 1
        else:  # true/false (boolean)
            if given is None:
                skipped += 1
                unanswered = True
            elif bool(given) == bool(truth):
                correct += 1
                ok = True
            else:
                wrong += 1
        review.append({
            "q": qs[i].get("q"), "options": qs[i].get("options"),
            "your": given, "correct": truth, "ok": ok, "unanswered": unanswered,
        })

    # token math — skips are free; only correct (+) and wrong (−) move the wallet
    net = round(correct * rwd - wrong * pen, 5)

    bonus = 0.0
    if (game == "quiz" and not timed_out and skipped == 0 and wrong == 0
            and correct == n and n > 0 and elapsed <= cfg["speed_secs"]):
        today = _now().strftime("%Y-%m-%d")
        u = await db.find_one_global("users", {"user_id": uid}, {"speed_bonus_day": 1}) or {}
        if u.get("speed_bonus_day") != today:
            bonus = cfg["speed_bonus"]
            await db.safe_update("users", {"user_id": uid},
                                 {"$set": {"speed_bonus_day": today}})

    total_delta = round(net + bonus, 5)
    if total_delta >= 0:
        await add_bgm(uid, total_delta)
    else:
        # never let a wrong-answer penalty push the balance below zero.
        # cut_bgm (a real deduction) — add_bgm sanitizes negatives to a no-op.
        bgm, _ = await get_balances(uid)
        await cut_bgm(uid, min(abs(total_delta), bgm))

    await db.safe_update("users", {"user_id": uid},
                         {"$inc": {"games_played": 1, "game_bgm": max(0.0, total_delta)}})
    from utils.missions import mark
    await mark(uid, "play_game")

    accuracy = (correct / n) if n else 0.0
    grade, tag = _grade(accuracy)
    earned = round(correct * rwd + bonus, 4)   # the positive, "headline" number
    xp = correct * 10 + (5 if bonus else 0)

    return {
        "ok": True, "game": game, "total_q": n,
        "correct": correct, "wrong": wrong, "skipped": skipped,
        "accuracy": round(accuracy, 3), "grade": grade, "tag": tag, "xp": xp,
        "earned": earned, "net": round(net, 4), "speed_bonus": bonus,
        "total": total_delta, "timed_out": timed_out, "review": review,
    }
