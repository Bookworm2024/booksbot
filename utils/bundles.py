"""
utils/bundles.py — "buy more, save more" purchase bonuses.

A purchase of N BGM grants a bonus % on top (free extra BGM), nudging larger
orders. Applied to both UPI and crypto purchases at credit time.
"""
# (threshold BGM, bonus fraction) — highest threshold first
BONUS_TIERS = [
    (250, 0.30),
    (100, 0.20),
    (50, 0.10),
    (0, 0.0),
]


def bonus_for(bgm: float) -> float:
    """Bonus BGM granted on top of a `bgm`-sized purchase."""
    for threshold, frac in BONUS_TIERS:
        if bgm >= threshold:
            return round(bgm * frac, 2)
    return 0.0


def bonus_pct(bgm: float) -> int:
    for threshold, frac in BONUS_TIERS:
        if bgm >= threshold:
            return int(frac * 100)
    return 0


def tiers_blurb() -> str:
    """Human-readable bonus ladder for the Buy menu."""
    parts = []
    for threshold, frac in sorted(BONUS_TIERS):
        if frac > 0:
            parts.append(f"{threshold}+ → +{int(frac*100)}%")
    return " · ".join(parts)
