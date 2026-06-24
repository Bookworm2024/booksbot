"""
utils/format.py — safe display formatting for token amounts.

The single source of truth for turning a BGM/BCN value into a human string.
Telegram chat (here) and the Mini-Apps (web_app/_fmt.js) must NEVER render a
balance in scientific notation — that is the "1e+21" bug: Python's ``:g`` and
JavaScript's ``String(n)`` both flip to exponential for large magnitudes.

`fmt_amount` renders the *actual* number, finite-guarded, no exponent, decimals
trimmed, thousands separators. `sanitize_amount` clamps a stored/computed value
into the legitimate range so corruption (bad input, overflow, NaN/inf) can never
propagate into the wallet.
"""
from __future__ import annotations

import math

# No legitimate balance or single transaction can exceed this. Anything larger
# is corruption (e.g. an admin typing "1e21", an overflow) and is clamped on the
# way in (sanitize_amount / amount validation) so the wallet stays sane.
MAX_AMOUNT: float = 1_000_000_000.0  # one billion tokens


def sanitize_amount(x) -> float:
    """Coerce any value to a finite, non-negative float within [0, MAX_AMOUNT].

    Used on balance reads and before crediting so a NaN/inf/garbage/absurd value
    can never enter or leave the wallet.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):  # NaN / +-inf
        return 0.0
    if v <= 0.0:
        return 0.0
    if v > MAX_AMOUNT:
        return MAX_AMOUNT
    return v


def clamp_amount(x, *, allow_negative: bool = False) -> float:
    """Like sanitize_amount but optionally keeps a (bounded) negative magnitude.
    Used for deltas that may legitimately be negative (e.g. game penalties)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    if not allow_negative and v < 0.0:
        return 0.0
    return max(-MAX_AMOUNT, min(MAX_AMOUNT, v))


def valid_amount(raw, *, allow_zero: bool = False) -> tuple[bool, float]:
    """Parse a user/admin-entered money value. Returns ``(ok, value)``.

    Rejects anything non-numeric, non-finite (``inf``/``nan``), negative, or
    above MAX_AMOUNT — the guard that stops "1e21"/"inf" from ever entering the
    economy. ``float("1e21")``/``float("inf")``/``float("nan")`` all parse, so a
    bare ``float()`` is not enough; this is.
    """
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return False, 0.0
    if not math.isfinite(v):
        return False, 0.0
    if v < 0 or (v == 0 and not allow_zero):
        return False, 0.0
    if v > MAX_AMOUNT:
        return False, 0.0
    return True, round(v, 3)


def fmt_amount(x, decimals: int = 2) -> str:
    """Human token amount — never scientific notation.

    Rounds to ``decimals`` places, drops trailing zeros, adds thousands
    separators. NaN/inf/None/garbage render as ``"0"``. Negative values keep
    their sign.

        fmt_amount(1e21)      -> "1,000,000,000,000,000,000,000"
        fmt_amount(12.5)      -> "12.5"
        fmt_amount(12.0)      -> "12"
        fmt_amount(0.125, 3)  -> "0.125"
        fmt_amount(float("inf")) -> "0"
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(v):
        return "0"
    decimals = max(0, int(decimals))
    neg = v < 0
    v = abs(round(v, decimals))
    if v == int(v):
        s = f"{int(v):,}"
    else:
        s = f"{v:,.{decimals}f}".rstrip("0").rstrip(".")
    return f"-{s}" if neg and v != 0 else s
