"""
tools/gen_questions.py — top up the games question bank toward a target per
category, using the Anthropic API. Idempotent & resumable: it only adds what's
missing (dedupe is enforced by the unique `qhash` index), so you can run it
repeatedly until every category reaches the target.

The games use a per-user ROTATION cursor, so a large bank makes the games feel
endless. This tool is how you grow each category to 5000 over time.

Categories (game[/level]):
  quiz/beginner, quiz/moderate, quiz/advanced, tf, guess, firstline, author

Provider:
  --provider free       (DEFAULT) uses the free bots.lt GPT API — NO key needed
  --provider anthropic  uses Claude (needs ANTHROPIC_API_KEY); higher quality

Requirements (env, same as the bot uses):
  MONGO_URL           — the bank lives in the same DB the bot reads (required)
  ANTHROPIC_API_KEY   — only for --provider anthropic
  ANTHROPIC_MODEL     — optional (defaults to config's model)

Usage (PowerShell):
  $env:MONGO_URL="mongodb+srv://..."
  python tools/gen_questions.py                          # free provider, fill to 5000
  python tools/gen_questions.py --target 5000 --games quiz,tf
  python tools/gen_questions.py --provider anthropic     # use Claude instead
  python tools/gen_questions.py --target 1000 --dry-run  # show the plan only

Notes:
  • Some categories have a natural content ceiling (e.g. 'firstline' — there are
    only so many genuinely famous opening lines). The tool stops a category early
    when it can't find anything new after several attempts; that's expected.
  • Run from the repo root so `config`/`database`/`utils` import cleanly.
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL  # noqa: E402
from database.connection import MongoManager  # noqa: E402
from utils.ai import DEFAULT_FREE_URL  # noqa: E402
from utils.games import VALID_GAMES, _normalize_seed  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("gen_questions")

_URL = "https://api.anthropic.com/v1/messages"

# set from CLI in main()
_PROVIDER = "free"      # "free" (bots.lt, no key) | "anthropic" (needs key)
_FREE_URL = DEFAULT_FREE_URL

# (game, level) → list of subtopics to diversify generation & reduce collisions.
CAT_SUBS = {
    ("quiz", "beginner"): [
        "world geography & capitals", "basic science & nature", "famous classic authors",
        "popular novels & their characters", "world history basics",
        "sports, music & pop culture", "animals & the human body",
        "food, mythology & everyday facts"],
    ("quiz", "moderate"): [
        "literature & literary movements", "world history & civilizations",
        "science & inventions", "geography & landmarks", "art, music & cinema",
        "mythology & religion", "politics, economics & society", "language & etymology"],
    ("quiz", "advanced"): [
        "classic & modern literature (deep cuts)", "world history (specific events & figures)",
        "advanced science (physics, chemistry, biology)", "philosophy & big ideas",
        "fine art & classical music", "geography & geopolitics (detailed)",
        "Nobel laureates, awards & world records", "ancient history & classical antiquity"],
    ("tf", None): [
        "literature & authors", "world history", "science & nature", "geography",
        "arts & culture", "mythology & religion", "famous people & inventions",
        "language & everyday facts"],
    ("guess", None): [
        "classic literature", "modern & contemporary fiction", "fantasy & science fiction",
        "mystery & thriller", "children's & young-adult", "world / translated literature"],
    ("firstline", None): [
        "classic novels", "20th-century fiction", "modern bestsellers",
        "world / translated literature"],
    ("author", None): [
        "classic literature", "modern fiction", "fantasy & science fiction",
        "mystery & crime", "non-fiction & philosophy", "poetry & drama",
        "world / translated literature", "children's & young-adult"],
}


def _schema_hint(game: str, level: str | None) -> str:
    if game == "tf":
        return ('{"game":"tf","q":"<clearly true or clearly false statement>","a":true}'
                "  (a is a JSON boolean)")
    if game == "guess":
        return ('{"game":"guess","q":"<1-2 sentence spoiler-free plot blurb, no title/author>",'
                '"options":{"A":"<title>","B":"<title>","C":"<title>","D":"<title>"},"a":"B"}')
    if game == "firstline":
        return ('{"game":"firstline","q":"<exact famous opening line, in quotes>",'
                '"options":{"A":"<title>","B":"<title>","C":"<title>","D":"<title>"},"a":"C"}')
    if game == "author":
        return ('{"game":"author","q":"Who wrote \'<real title>\'?",'
                '"options":{"A":"<author>","B":"<author>","C":"<author>","D":"<author>"},"a":"A"}')
    return ('{"game":"quiz","level":"%s","q":"<question>",'
            '"options":{"A":"<o>","B":"<o>","C":"<o>","D":"<o>"},"a":"D"}' % (level or "beginner"))


def _prompt(game: str, level: str | None, sub: str, n: int) -> str:
    if game == "quiz":
        diff = {"beginner": "easy, widely-known facts for a general audience",
                "moderate": "moderately challenging, for a well-read adult",
                "advanced": "hard, for experts and serious readers"}[level]
        lvl = f'Difficulty: {level} ({diff}). Set "level" to "{level}" on every object.\n'
        style = "Four options A-D, exactly ONE correct; distractors plausible but clearly wrong."
    elif game == "tf":
        lvl = ""
        style = "Roughly half true, half false. Each statement unambiguously decidable."
    elif game == "guess":
        lvl = ""
        style = ("q = a vivid 1-2 sentence plot blurb (NO title/author, no big spoilers); "
                 "four real book titles, exactly one matching.")
    elif game == "firstline":
        lvl = ""
        style = ("q = a REAL verbatim, genuinely famous opening line (keep quotes); "
                 "four real titles, only well-known verifiable lines.")
    else:  # author
        lvl = ""
        style = "q = \"Who wrote 'TITLE'?\"; four real authors, exactly one correct."
    return (
        f"You are an expert quizmaster and librarian building a trivia bank for a books bot.\n"
        f"Produce EXACTLY {n} {game.upper()} questions on the theme: \"{sub}\".\n{lvl}{style}\n\n"
        "ACCURACY IS CRITICAL (this moves a real token economy):\n"
        "- Every fact must be TRUE and verifiable; if unsure, omit it.\n"
        "- Exactly one correct answer; no equivalent/duplicate options.\n"
        "- Question under ~140 chars; options short; vary widely; no duplicates.\n"
        "- Straight quotes; plain text.\n\n"
        "OUTPUT ONLY a JSON array (no prose, no code fences). Each element like:\n"
        f"{_schema_hint(game, level)}"
    )


def _extract_array(text: str) -> list:
    if not text:
        return []
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    i, j = text.find("["), text.rfind("]")
    if i == -1 or j == -1 or j < i:
        return []
    try:
        data = json.loads(text[i:j + 1])
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


async def _generate(session: aiohttp.ClientSession, game: str, level: str | None,
                    sub: str, n: int) -> list:
    prompt = _prompt(game, level, sub, n) + "\nReturn ONLY a JSON array."
    if _PROVIDER == "free":
        try:
            async with session.get(_FREE_URL, params={"message": prompt},
                                   timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200:
                    logger.warning("  Free AI %s", r.status)
                    return []
                raw = await r.text()
        except Exception as exc:  # noqa: BLE001
            logger.warning("  free call failed: %s", exc)
            return []
        try:
            data = json.loads(raw)
            text = data.get("response", "") if isinstance(data, dict) else raw
        except Exception:  # noqa: BLE001
            text = raw
        return _extract_array(text)
    # anthropic
    payload = {"model": ANTHROPIC_MODEL, "max_tokens": 4500,
               "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    try:
        async with session.post(_URL, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                logger.warning("  Anthropic %s: %s", r.status, (await r.text())[:160])
                return []
            data = await r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return _extract_array(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("  API call failed: %s", exc)
        return []


async def _count(db, game: str, level: str | None) -> int:
    flt = {"game": game}
    if level:
        flt["level"] = level
    return await db.count_global("questions", flt)


async def fill_category(db, session, game, level, target, batch, max_batches):
    subs = CAT_SUBS[(game, level)]
    label = game + (f"/{level}" if level else "")
    have = await _count(db, game, level)
    if have >= target:
        logger.info("%-18s already at %d/%d — skipping.", label, have, target)
        return 0
    logger.info("%-18s %d/%d — generating…", label, have, target)
    added = 0
    empty_streak = 0
    for b in range(max_batches):
        if have + added >= target:
            break
        sub = subs[b % len(subs)]
        items = await _generate(session, game, level, sub, batch)
        new_here = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            raw.setdefault("game", game)
            if level:
                raw.setdefault("level", level)
            norm = _normalize_seed(raw)
            if not norm:
                continue
            if await db.safe_insert("questions", norm):  # False on dup qhash
                new_here += 1
                added += 1
                if have + added >= target:
                    break
        empty_streak = empty_streak + 1 if new_here == 0 else 0
        logger.info("  [%s] batch %d (%s): +%d new (total +%d, now %d)",
                    label, b + 1, sub, new_here, added, have + added)
        if empty_streak >= 5:
            logger.info("  %s: no new questions in 5 batches — likely at content ceiling. Stopping.", label)
            break
    return added


async def run(target, games, batch, max_batches, dry):
    db = await MongoManager.get()

    plan = [(g, lv) for (g, lv) in CAT_SUBS if g in games]
    logger.info("Target: %d per category. Categories: %s\n",
                target, ", ".join(g + (f"/{lv}" if lv else "") for g, lv in plan))
    if dry:
        for g, lv in plan:
            have = await _count(db, g, lv)
            need = max(0, target - have)
            logger.info("  %-18s have %d -> need %d", g + (f"/{lv}" if lv else ""), have, need)
        logger.info("\n(dry run - nothing generated)")
        return

    logger.info("Using provider: %s%s\n", _PROVIDER,
                "" if _PROVIDER != "free" else f" ({_FREE_URL})")
    if _PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is not set (needed for --provider anthropic).")

    total_added = 0
    async with aiohttp.ClientSession() as session:
        for g, lv in plan:
            total_added += await fill_category(db, session, g, lv, target, batch, max_batches)

    logger.info("\nDone. Added %d new questions.", total_added)
    grand = await db.count_global("questions")
    logger.info("Bank now holds %d questions total.", grand)


def main():
    ap = argparse.ArgumentParser(description="Top up the games question bank via the Anthropic API.")
    ap.add_argument("--target", type=int, default=5000, help="Questions per category (default 5000).")
    ap.add_argument("--games", default="all",
                    help="Comma list of games to fill (quiz,tf,guess,firstline,author) or 'all'.")
    ap.add_argument("--batch", type=int, default=45, help="Questions requested per API call.")
    ap.add_argument("--max-batches", type=int, default=400,
                    help="Safety cap on API calls per category.")
    ap.add_argument("--provider", choices=("free", "anthropic"), default="free",
                    help="AI backend: 'free' (bots.lt, no key) or 'anthropic' (needs key).")
    ap.add_argument("--free-url", default=DEFAULT_FREE_URL, help="Free API base URL.")
    ap.add_argument("--dry-run", action="store_true", help="Show the plan; generate nothing.")
    args = ap.parse_args()

    global _PROVIDER, _FREE_URL
    _PROVIDER = args.provider
    _FREE_URL = args.free_url

    if args.games.strip().lower() == "all":
        games = set(VALID_GAMES)
    else:
        games = {g.strip() for g in args.games.split(",") if g.strip() in VALID_GAMES}
    if not games:
        raise SystemExit("No valid games selected.")

    asyncio.run(run(args.target, games, args.batch, args.max_batches, args.dry_run))


if __name__ == "__main__":
    main()
