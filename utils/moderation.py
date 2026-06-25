"""
utils/moderation.py — lightweight auto-moderation for user content.

A cheap, dependency-free filter for public user text (club posts, reviews):
a configurable banned-word list plus spam heuristics (too many links, shouting,
character spam). Admins manage the word list + on/off live from the panel.

Stored in kv: `mod_enabled` (bool, default True), `mod_banned` (list[str]).
"""
import re

from database.connection import MongoManager

# seed list; admins extend/replace it live via the panel
DEFAULT_BANNED = [
    "child porn", "cp link", "onlyfans", "sex video", "xxx",
    "free nitro", "crypto giveaway", "double your", "t.me/+joinscam",
]

_URL_RE = re.compile(r"(https?://|www\.|t\.me/|@[A-Za-z0-9_]{4,})", re.I)
_REPEAT_RE = re.compile(r"(.)\1{7,}")  # same char 8+ times in a row


async def is_enabled() -> bool:
    db = await MongoManager.get()
    return bool(await db.kv_get("mod_enabled", True))


async def set_enabled(on: bool) -> None:
    db = await MongoManager.get()
    await db.kv_set("mod_enabled", bool(on))


async def banned_words() -> list[str]:
    db = await MongoManager.get()
    words = await db.kv_get("mod_banned", None)
    if words is None:
        return list(DEFAULT_BANNED)
    return [str(w) for w in words]


async def set_banned_words(words: list[str]) -> None:
    db = await MongoManager.get()
    clean = sorted({w.strip().lower() for w in words if w and w.strip()})
    await db.kv_set("mod_banned", clean)


async def add_banned(word: str) -> bool:
    w = (word or "").strip().lower()
    if not w:
        return False
    words = await banned_words()
    if w in (x.lower() for x in words):
        return False
    words.append(w)
    await set_banned_words(words)
    return True


async def remove_banned(word: str) -> bool:
    w = (word or "").strip().lower()
    words = await banned_words()
    new = [x for x in words if x.lower() != w]
    if len(new) == len(words):
        return False
    await set_banned_words(new)
    return True


def _heuristic_reason(text: str) -> str | None:
    t = text or ""
    if len(_URL_RE.findall(t)) >= 3:
        return "too many links"
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.75:
        return "excessive shouting"
    if _REPEAT_RE.search(t):
        return "spammy character repetition"
    return None


def _banned_hit(text: str, words: list[str]) -> str | None:
    low = (text or "").lower()
    for w in words:
        w = w.strip().lower()
        if not w:
            continue
        # word-boundary match for single tokens; plain substring for phrases
        pat = r"\b" + re.escape(w) + r"\b" if " " not in w else re.escape(w)
        if re.search(pat, low):
            return "blocked term"
    return None


async def check(text: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=True means allowed. Reason is a short user-facing
    label when blocked. A no-op (always ok) when moderation is disabled."""
    if not (text or "").strip():
        return False, "empty message"
    if not await is_enabled():
        return True, ""
    reason = _heuristic_reason(text)
    if reason:
        return False, reason
    reason = _banned_hit(text, await banned_words())
    if reason:
        return False, reason
    return True, ""
