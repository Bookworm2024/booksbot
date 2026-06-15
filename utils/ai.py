"""
utils/ai.py — AI backend for recommendations, summaries & genre tagging.

Pluggable provider, chosen at RUNTIME (admin can switch it from /admin — no
redeploy). Config lives in Mongo `kv` under `ai:*`:

  ai:provider       "free" (default) | "anthropic" | "off"
  ai:free_url       base URL of the free GPT API (default below)
  ai:anthropic_key  Claude API key (falls back to env ANTHROPIC_API_KEY)
  ai:model          Claude model id (falls back to env ANTHROPIC_MODEL)

Providers:
  • free      — bots.lt free GPT endpoint: GET <url>?message=<prompt> →
                {"success":true,"response":"..."}. No key needed.
  • anthropic — Claude Messages API (needs a key).
  • off       — AI features disabled.

All public helpers (recommend_titles / summarize_book / classify_genre) go
through ai_complete(), so switching the provider switches every feature at once.
"""
import json
import logging
import re

import aiohttp

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from database.connection import MongoManager

logger = logging.getLogger(__name__)

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_FREE_URL = "https://bots.lt/Apis/AI/gpt.php"
_NUM_RE = re.compile(r"^\s*\d+[\.\)]\s*")
_PREAMBLE = ("here are", "here is", "sure", "okay", "certainly", "of course",
             "these are", "below are", "i'd recommend", "i recommend")


# ── runtime config (Mongo kv) ───────────────────────────────────────────────────
async def get_ai_config() -> dict:
    db = await MongoManager.get()
    provider = await db.kv_get("ai:provider", None) or "free"
    return {
        "provider": provider,
        "free_url": (await db.kv_get("ai:free_url", None)) or DEFAULT_FREE_URL,
        "anthropic_key": (await db.kv_get("ai:anthropic_key", None)) or ANTHROPIC_API_KEY,
        "model": (await db.kv_get("ai:model", None)) or ANTHROPIC_MODEL,
    }


async def set_ai_config(key: str, value) -> None:
    if key not in ("provider", "free_url", "anthropic_key", "model"):
        raise ValueError(f"unknown ai config key: {key}")
    db = await MongoManager.get()
    await db.kv_set(f"ai:{key}", value)


async def ai_enabled() -> bool:
    """True if AI features can run with the current config."""
    cfg = await get_ai_config()
    if cfg["provider"] == "off":
        return False
    if cfg["provider"] == "anthropic":
        return bool(cfg["anthropic_key"])
    return bool(cfg["free_url"])   # free provider — always on when a URL is set


# ── provider calls ──────────────────────────────────────────────────────────────
async def _free_call(prompt: str, base_url: str) -> str | None:
    if not base_url:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(base_url, params={"message": prompt}) as r:
                if r.status != 200:
                    logger.warning("Free AI %s: %s", r.status, (await r.text())[:160])
                    return None
                raw = await r.text()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Free AI call failed: %s", exc)
        return None
    # response is JSON {"response": "..."}; fall back to raw text if not JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            text = (data.get("response") or data.get("answer") or data.get("message") or "").strip()
        else:
            text = ""
    except Exception:  # noqa: BLE001
        text = raw.strip()
    return text or None


async def _anthropic_call(prompt: str, max_tokens: int, key: str, model: str) -> str | None:
    if not key:
        return None
    payload = {"model": model, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(_ANTHROPIC_URL, json=payload, headers=headers) as r:
                if r.status != 200:
                    logger.warning("Anthropic %s: %s", r.status, (await r.text())[:200])
                    return None
                data = await r.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic call failed: %s", exc)
        return None


async def ai_complete(prompt: str, max_tokens: int = 900) -> str | None:
    """Single-shot completion via the configured provider. None on failure/disabled."""
    cfg = await get_ai_config()
    provider = cfg["provider"]
    if provider == "off":
        return None
    if provider == "anthropic":
        return await _anthropic_call(prompt, max_tokens, cfg["anthropic_key"], cfg["model"])
    return await _free_call(prompt, cfg["free_url"])


# Backwards-compatible alias (older callers used utils.ai._call).
async def _call(prompt: str, max_tokens: int = 900) -> str | None:
    return await ai_complete(prompt, max_tokens)


# ── public helpers ──────────────────────────────────────────────────────────────
def _recommend_prompt(genre: str) -> str:
    return (
        f"List exactly 100 well-known {genre} books. "
        "Output ONLY a numbered list, one book per line, formatted "
        "'Title — Author'. No preamble, no commentary. "
        f"If \"{genre}\" is not a real book genre or category, output exactly: INVALID"
    )


async def recommend_titles(genre: str) -> list[str] | None:
    """~100 titles in a genre, or None if invalid/unavailable (caller refunds)."""
    genre = (genre or "").strip()
    if not genre or not await ai_enabled():
        return None
    text = await ai_complete(_recommend_prompt(genre), max_tokens=2000)
    if not text or "INVALID" in text[:20].upper():
        return None
    titles = []
    for line in text.splitlines():
        line = _NUM_RE.sub("", line).strip(" -•*\t").strip()
        low = line.lower()
        if not line or len(line) <= 2:
            continue
        if low.endswith(":") or any(low.startswith(p) for p in _PREAMBLE):
            continue
        titles.append(line)
    return titles if len(titles) >= 10 else None


async def classify_genre(title: str) -> str | None:
    """Classify a book title into one of files.GENRES (or 'Other'). None if unavailable."""
    from utils.files import GENRES
    title = (title or "").strip()
    if not title or not await ai_enabled():
        return None
    prompt = (f"Classify the book titled \"{title}\" into exactly ONE of these "
              f"genres: {', '.join(GENRES)}. Reply with ONLY the genre, nothing else.")
    text = await ai_complete(prompt, max_tokens=20)
    if not text:
        return None
    low = text.lower()
    for g in GENRES:
        if g.lower() in low:
            return g
    return "Other"


async def summarize_book(title: str) -> str | None:
    """HTML-formatted summary of a book, or None if unknown/unavailable."""
    title = (title or "").strip()
    if not title or not await ai_enabled():
        return None
    prompt = (
        f"Give a concise, spoiler-light summary of the book \"{title}\". "
        "If you don't recognize it as a real published book, reply with exactly: UNKNOWN.\n"
        "Otherwise use EXACTLY this template with these labels:\n"
        "Overview: <2-3 sentences>\n"
        "Themes: <comma-separated>\n"
        "Best for: <who'd enjoy it>\n"
        "Takeaways: <three '- ' bullet lines>\n"
        "Keep it under 180 words. Plain text only, no markdown headers."
    )
    text = await ai_complete(prompt, max_tokens=700)
    if not text or "UNKNOWN" in text[:30].upper():
        return None
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for lbl in ("Overview:", "Themes:", "Best for:", "Takeaways:"):
            if line.startswith(lbl):
                line = line.replace(lbl, f"<b>{lbl}</b>", 1)
                break
        out.append(line)
    return "\n".join(out)
