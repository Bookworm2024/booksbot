"""
utils/ai.py — AI backend for recommendations, summaries & genre tagging.

Pluggable provider, chosen at RUNTIME (admin can switch it from /admin — no
redeploy). Config lives in Mongo `kv` under `ai:*`:

  ai:provider         "free" (default) | "anthropic" | "off"
  ai:free_url         base URL of the free GPT API (default below)
  ai:anthropic_key    Claude API key (falls back to env ANTHROPIC_API_KEY)
  ai:model            Claude model id (falls back to env ANTHROPIC_MODEL)
  ai:webhook_enabled  bool — when True, AI requests go to a custom webhook
  ai:webhook_url      the webhook endpoint the AI API is reachable at

Providers:
  • free      — bots.lt free GPT endpoint: GET <url>?message=<prompt> →
                {"success":true,"response":"..."}. No key needed.
  • anthropic — Claude Messages API (needs a key).
  • off       — AI features disabled.

Webhook mode (a per-AI toggle, set in /admin → 🤖 AI Engine):
  When enabled with a URL set, EVERY completion is POSTed to that webhook as
  JSON ({"message","prompt","max_tokens"}) and the reply text is read from the
  response (response/answer/message/result/text/content/output/data, or raw
  body). This lets the operator point the bot at any custom AI backend / relay
  without a redeploy. Webhook mode takes precedence over the provider (except
  "off", which always wins and disables AI).

All public helpers (recommend_titles / summarize_book / classify_genre) go
through ai_complete(), so switching the provider — or flipping webhook mode —
switches every feature at once.
"""
import json
import logging
import re
from html import escape

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
        "webhook_enabled": bool(await db.kv_get("ai:webhook_enabled", False)),
        "webhook_url": (await db.kv_get("ai:webhook_url", None)) or "",
    }


async def set_ai_config(key: str, value) -> None:
    if key not in ("provider", "free_url", "anthropic_key", "model",
                   "webhook_enabled", "webhook_url"):
        raise ValueError(f"unknown ai config key: {key}")
    db = await MongoManager.get()
    await db.kv_set(f"ai:{key}", value)


def _webhook_active(cfg: dict) -> bool:
    return bool(cfg.get("webhook_enabled")) and bool(cfg.get("webhook_url"))


async def ai_enabled() -> bool:
    """True if AI features can run with the current config."""
    cfg = await get_ai_config()
    if cfg["provider"] == "off":
        return False
    if _webhook_active(cfg):
        return True   # custom webhook takes over for every feature
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


_WEBHOOK_FIELDS = ("response", "answer", "message", "result", "text",
                   "content", "output", "data", "reply", "completion")


async def _webhook_call(prompt: str, url: str, max_tokens: int) -> str | None:
    """Webhook mode: POST the prompt as JSON to a custom AI endpoint and read the
    reply text back. Tolerant of however the endpoint shapes its response — we try
    a list of common reply fields, then fall back to the raw body."""
    if not url:
        return None
    payload = {"message": prompt, "prompt": prompt, "max_tokens": max_tokens}
    try:
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload) as r:
                if r.status != 200:
                    logger.warning("AI webhook %s: %s", r.status, (await r.text())[:200])
                    return None
                raw = await r.text()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI webhook call failed: %s", exc)
        return None
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw.strip() or None
    if isinstance(data, str):
        return data.strip() or None
    if isinstance(data, dict):
        for f in _WEBHOOK_FIELDS:
            val = data.get(f)
            if isinstance(val, str) and val.strip():
                return val.strip()
            # some relays nest the text one level deep (e.g. {"data":{"text":...}})
            if isinstance(val, dict):
                for g in _WEBHOOK_FIELDS:
                    inner = val.get(g)
                    if isinstance(inner, str) and inner.strip():
                        return inner.strip()
    return None


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
    if _webhook_active(cfg):
        return await _webhook_call(prompt, cfg["webhook_url"], max_tokens)
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


def _parse_titles(text: str, minimum: int = 10) -> list[str] | None:
    """Turn an LLM 'numbered list of Title — Author' into a clean list."""
    titles = []
    for line in (text or "").splitlines():
        line = _NUM_RE.sub("", line).strip(" -•*\t").strip()
        low = line.lower()
        if not line or len(line) <= 2:
            continue
        if low.endswith(":") or any(low.startswith(p) for p in _PREAMBLE):
            continue
        titles.append(line)
    return titles if len(titles) >= minimum else None


async def recommend_titles(genre: str) -> list[str] | None:
    """~100 titles in a genre, or None if invalid/unavailable (caller refunds)."""
    genre = (genre or "").strip()
    if not genre or not await ai_enabled():
        return None
    text = await ai_complete(_recommend_prompt(genre), max_tokens=2000)
    if not text or "INVALID" in text[:20].upper():
        return None
    return _parse_titles(text, minimum=10)


async def similar_titles(title: str) -> list[str] | None:
    """~30 books similar to a given title (same genre/themes/vibe)."""
    title = (title or "").strip()
    if not title or not await ai_enabled():
        return None
    prompt = (f'List exactly 30 books similar to "{title}" — same genre, themes or '
              "vibe — excluding that book itself. Output ONLY a numbered list, one per "
              "line, 'Title — Author'. No commentary. "
              f'If "{title}" is not a real book, output exactly: INVALID')
    text = await ai_complete(prompt, max_tokens=1300)
    if not text or "INVALID" in text[:20].upper():
        return None
    return _parse_titles(text, minimum=5)


async def mood_titles(mood: str) -> list[str] | None:
    """~40 books matching a mood / vibe (e.g. 'cozy rainy-day', 'fast thriller')."""
    mood = (mood or "").strip()
    if not mood or not await ai_enabled():
        return None
    prompt = (f'List exactly 40 books that fit this mood / vibe: "{mood}". '
              "Output ONLY a numbered list, one per line, 'Title — Author'. No "
              f'commentary. If "{mood}" is not a usable mood/theme, output exactly: INVALID')
    text = await ai_complete(prompt, max_tokens=1600)
    if not text or "INVALID" in text[:20].upper():
        return None
    return _parse_titles(text, minimum=5)


async def clean_titles(raw_names: list[str]) -> dict[str, str]:
    """Turn messy archive FILENAMES into clean human book titles, in ONE call.
    e.g. 'OceanofPDF_Atomic_Habits_' → 'Atomic Habits'. Returns {raw: clean} for
    every name it could clean (missing keys → caller keeps its basic-cleaned name)."""
    raw_names = [n for n in (raw_names or []) if (n or "").strip()]
    if not raw_names or not await ai_enabled():
        return {}
    lines = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(raw_names))
    prompt = (
        "Below are messy ebook FILENAMES, one per numbered line. For EACH line, output "
        "the clean, human-readable book TITLE only — strip site names (OceanofPDF, "
        "Z-Library, LibGen, PDFDrive, Anna's Archive), @handles, URLs, file extensions, "
        "underscores, stray ids, and edition/format junk. Keep the real title (and a clear "
        "subtitle if present). Do NOT invent or translate. Output EXACTLY one line per "
        "input, same count and order, formatted 'N. Clean Title', and nothing else.\n\n"
        + lines)
    text = await ai_complete(prompt, max_tokens=min(2000, 60 * len(raw_names) + 120))
    if not text:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        val = m.group(2).strip().strip('"').strip("•*").strip()
        if 0 <= idx < len(raw_names) and len(val) >= 2 and val.upper() != "INVALID":
            out[raw_names[idx]] = val[:120]
    return out


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
    low = text.lower().strip()
    # Exact match first (most reliable). Then substring, but LONGEST genre name
    # first so "Non-Fiction" isn't swallowed by the "fiction" substring of "Fiction".
    for g in GENRES:
        if g.lower() == low:
            return g
    for g in sorted(GENRES, key=len, reverse=True):
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
        # Escape the raw AI text first (it can contain &, <, > — e.g. "Crime &
        # Punishment") so it doesn't break Telegram's HTML parser; the labels
        # have no special chars, so we can still bold them after escaping.
        line = escape(line)
        for lbl in ("Overview:", "Themes:", "Best for:", "Takeaways:"):
            if line.startswith(lbl):
                line = line.replace(lbl, f"<b>{lbl}</b>", 1)
                break
        out.append(line)
    return "\n".join(out)
