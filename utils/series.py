"""
utils/series.py — lightweight book-series detection over the file archive.

Heuristic only (no metadata): parse a volume number out of a title ("Harry
Potter 3", "Dune Book 2", "Wheel of Time #4", "Foundation, Part II"), derive the
series base name, then find sibling volumes already in the archive. Used for the
🔗 Series finder in Discover and the "📚 Next in series" nudge after a download.
"""
import re

_VOL_RE = re.compile(
    r"(?:\b(?:book|bk|vol\.?|volume|part|pt\.?|episode|ep\.?|no\.?|number)\s*|#)\s*"
    r"(\d{1,3})\b", re.IGNORECASE)
_TRAIL_RE = re.compile(r"\b(\d{1,3})\s*$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}
_ROMAN_RE = re.compile(r"(?:\b(?:book|vol\.?|volume|part)\s*|#)\s*"
                       r"(i{1,3}|iv|v|vi{0,3}|ix|xi{0,2}|xii)\b", re.IGNORECASE)
_NORM = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _NORM.sub("", (s or "").lower())


def _clean_base(base: str) -> str:
    # drop dangling series words / separators left after removing the number
    base = re.sub(r"\b(book|bk|vol\.?|volume|part|pt\.?|episode|ep\.?|no\.?|number)\b",
                  " ", base, flags=re.IGNORECASE)
    base = re.sub(r"[\s,\-_:#.]+", " ", base)
    return " ".join(base.split()).strip()


def parse_series(title: str) -> tuple[str, int] | None:
    """Return (base_name, volume_number) if the title looks like a numbered series
    entry, else None. Years (>= 1000) and bare-number titles are rejected."""
    t = (title or "").strip()
    if not t:
        return None
    # explicit "book/vol/part N" or "#N"
    m = _VOL_RE.search(t)
    if m:
        num = int(m.group(1))
        base = _clean_base(t[:m.start()] + " " + t[m.end():])
        if 1 <= num <= 99 and len(base) >= 3 and re.search(r"[a-z]", base, re.I):
            return base, num
    # roman numerals after a series word
    rm = _ROMAN_RE.search(t)
    if rm:
        num = _ROMAN.get(rm.group(1).lower())
        base = _clean_base(t[:rm.start()] + " " + t[rm.end():])
        if num and len(base) >= 3 and re.search(r"[a-z]", base, re.I):
            return base, num
    # trailing standalone number (avoid years like 1984 / 2020)
    tm = _TRAIL_RE.search(t)
    if tm:
        num = int(tm.group(1))
        if 1 <= num <= 50:
            base = _clean_base(t[:tm.start()])
            if len(base) >= 3 and re.search(r"[a-z]", base, re.I):
                return base, num
    return None


def _same_series(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na.startswith(nb) or nb.startswith(na)


async def find_series(file: dict) -> list[dict]:
    """Ordered list of archive files in the same series as `file` (inclusive),
    sorted by volume number. Empty if the title isn't a recognizable series entry
    or no siblings are found."""
    parsed = parse_series(file.get("name", ""))
    if not parsed:
        return []
    base, _ = parsed
    from utils.files import search_any
    pool = await search_any(base.split(), limit=60)
    found: dict[int, dict] = {}
    for cand in pool:
        cp = parse_series(cand.get("name", ""))
        if not cp:
            continue
        cbase, cnum = cp
        if _same_series(base, cbase) and cnum not in found:
            found[cnum] = cand
    if len(found) < 2:
        return []
    return [found[n] for n in sorted(found)]


async def next_volume(file: dict) -> dict | None:
    """The next volume after `file` in its series, or None."""
    parsed = parse_series(file.get("name", ""))
    if not parsed:
        return None
    _, cur = parsed
    siblings = await find_series(file)
    nxt = None
    for s in siblings:
        sp = parse_series(s.get("name", ""))
        if not sp:
            continue
        n = sp[1]
        if n > cur and (nxt is None or n < parse_series(nxt["name"])[1]):
            nxt = s
    return nxt
