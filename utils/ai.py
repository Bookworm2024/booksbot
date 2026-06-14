"""
utils/ai.py — Claude-backed book recommendations.

recommend_titles(genre) asks Claude for ~100 titles in a genre. Returns the
list, or None if the genre is invalid / the API is unavailable (the caller
refunds on None). Uses the Anthropic Messages API over aiohttp; the key is read
from config.ANTHROPIC_API_KEY (set it in the host env).
"""
import logging
import re

import aiohttp

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

logger = logging.getLogger(__name__)

_URL = "https://api.anthropic.com/v1/messages"
_NUM_RE = re.compile(r"^\s*\d+[\.\)]\s*")


def _prompt(genre: str) -> str:
    return (
        f"List exactly 100 well-known {genre} books. "
        "Output ONLY a numbered list, one book per line, formatted "
        "'Title — Author'. No preamble, no commentary. "
        f"If \"{genre}\" is not a real book genre or category, output exactly: INVALID"
    )


async def recommend_titles(genre: str) -> list[str] | None:
    genre = (genre or "").strip()
    if not genre or not ANTHROPIC_API_KEY:
        return None
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": _prompt(genre)}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(_URL, json=payload, headers=headers) as r:
                if r.status != 200:
                    logger.warning("Anthropic API %s: %s", r.status, (await r.text())[:200])
                    return None
                data = await r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic call failed: %s", exc)
        return None

    try:
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    except Exception:  # noqa: BLE001
        return None

    if not text or "INVALID" in text[:20].upper():
        return None

    titles = []
    for line in text.splitlines():
        line = _NUM_RE.sub("", line).strip(" -•*").strip()
        if line and len(line) > 2:
            titles.append(line)
    return titles if len(titles) >= 10 else None


async def _call(prompt: str, max_tokens: int = 900) -> str | None:
    """Single-shot Claude text call. Returns the text or None on failure."""
    if not ANTHROPIC_API_KEY:
        return None
    payload = {"model": ANTHROPIC_MODEL, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(_URL, json=payload, headers=headers) as r:
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


async def summarize_book(title: str) -> str | None:
    """Return an HTML-formatted summary of a book, or None if unknown/unavailable."""
    title = (title or "").strip()
    if not title:
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
    text = await _call(prompt, max_tokens=700)
    if not text or "UNKNOWN" in text[:30].upper():
        return None
    # light formatting → HTML
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
