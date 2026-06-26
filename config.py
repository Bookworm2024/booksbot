"""
config.py — BooksBot configuration.

All settings come from environment variables (.env locally; the platform's
env panel on Koyeb/Render/Railway/VPS). Parsing is defensive: a typo in one
variable must never crash module import — it would mask the friendly summary
printed by validate_runtime_config() at startup.
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)


# ── parsing helpers ──────────────────────────────────────────────────────────
def _csv_ints(raw: str) -> list[int]:
    out: list[int] = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except (ValueError, TypeError):
            _log.warning("Ignoring non-integer entry %r", tok)
    return out


def _csv_strs(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def _int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name) or default)
    except (ValueError, TypeError):
        _log.warning("Invalid %s; defaulting to %d.", name, default)
        return default


def _float_env(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
    except (ValueError, TypeError):
        _log.warning("Invalid %s; defaulting to %s.", name, default)
        return default


def _bool(name: str, default: bool = False) -> bool:
    val = (os.getenv(name) or "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


# ── core ─────────────────────────────────────────────────────────────────────
BOT_TOKEN: str       = os.getenv("BOT_TOKEN", "")
BOT_USERNAME: str    = os.getenv("BOT_USERNAME", "getfreebooksbot").lstrip("@")
SUPER_ADMIN_ID: int  = _int("SUPER_ADMIN_ID", 6011680723)
# Normal admins (super admin is always implicitly an admin too).
ADMIN_IDS: list[int] = sorted(set(_csv_ints(os.getenv("ADMIN_IDS", "")) + [SUPER_ADMIN_ID]))

# Optional custom Bot API server (a coloured-button-capable fork). Leave unset
# to use Telegram's official api.telegram.org.
TELEGRAM_API_BASE: str = os.getenv("TELEGRAM_API_BASE", "").strip()
# Master switch for coloured button styling. The bot ALWAYS builds coloured
# keyboards; on a vanilla API server set this False so the style fields are
# stripped before send (vanilla rejects unknown fields on some methods).
COLORED_BUTTONS: bool = _bool("COLORED_BUTTONS", True)


# ── database ─────────────────────────────────────────────────────────────────
# Accept either a single MONGO_URL or a comma list MONGO_URLS, plus numbered
# MONGO_URL_1.. for a multi-cluster waterfall (free Atlas 512MB tiers).
def _mongo_urls() -> list[str]:
    urls = _csv_strs(os.getenv("MONGO_URLS", "")) or _csv_strs(os.getenv("MONGO_URL", ""))
    i = 1
    while True:
        u = os.getenv(f"MONGO_URL_{i}")
        if not u:
            break
        urls.append(u.strip())
        i += 1
    # de-dup, preserve order
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


MONGO_URLS: list[str] = _mongo_urls()
MONGO_DB_NAME: str    = os.getenv("MONGO_DB_NAME", "booksbot")


# ── channels ─────────────────────────────────────────────────────────────────
# Required-membership gate shown on /start (comma list of @usernames).
REQUIRED_CHANNELS: list[str] = _csv_strs(
    os.getenv("REQUIRED_CHANNELS", "@Bookslibraryofficial,@eternalmantra,@thesciencelabs")
)
# Private channel holding the ~30k files to be indexed (numeric -100... id).
FILE_CHANNEL_ID: int = _int("FILE_CHANNEL_ID", 0)
# Where the bot posts new-user / activity logs.
LOG_CHANNEL_ID: int  = _int("LOG_CHANNEL_ID", 0)


# ── Telethon backfill (tools/backfill.py only) ───────────────────────────────
API_ID: int           = _int("API_ID", 0)
API_HASH: str         = os.getenv("API_HASH", "")
TELETHON_SESSION: str = os.getenv("TELETHON_SESSION", "")


# ── web / mini apps ──────────────────────────────────────────────────────────
PORT: int            = _int("PORT", 8080)
# Public HTTPS base (e.g. https://booksbot.koyeb.app). Required for Mini Apps —
# Telegram only opens web_app buttons over HTTPS.
def _public_url() -> str:
    """Normalize BOT_PUBLIC_URL bulletproof-ly. Telegram only accepts HTTPS
    web_app URLs, so we FORCE an https scheme: strip whatever scheme is present
    (even a typo like 'ttps://' or 'http://') and re-add 'https://'. Also strip a
    trailing '/health' (a common copy-paste slip) and trailing slashes."""
    u = os.getenv("BOT_PUBLIC_URL", "").strip().rstrip("/")
    if not u:
        return ""
    if "://" in u:
        u = u.split("://", 1)[1]   # drop any (possibly malformed) scheme
    u = "https://" + u
    if u.endswith("/health"):
        u = u[: -len("/health")].rstrip("/")
    return u


BOT_PUBLIC_URL: str  = _public_url()


# ── economy ──────────────────────────────────────────────────────────────────
BCN_EXPIRY_SECONDS: int = _int("BCN_EXPIRY_SECONDS", 86400)


# ── payments (Buy BGM) ───────────────────────────────────────────────────────
UPI_ID: str            = os.getenv("UPI_ID", "sendrajbooks@fam")
PAYMENT_QR_URL: str    = os.getenv("PAYMENT_QR_URL", "")  # static QR image URL
# Email-monitored UPI auto-verify (FamPay receipts → rajsom8877@gmail.com).
IMAP_HOST: str         = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER: str         = os.getenv("IMAP_USER", "")          # rajsom8877@gmail.com
IMAP_PASSWORD: str     = os.getenv("IMAP_PASSWORD", "")      # Gmail app password
FAMPAY_SENDER: str     = os.getenv("FAMPAY_SENDER", "no-reply@famapp.in")
EMAIL_LOG_CHANNEL: int = _int("EMAIL_LOG_CHANNEL", 0)
BGM_PRICE_INR: float   = _float_env("BGM_PRICE_INR", 2.0)
BGM_PRICE_USD: float   = _float_env("BGM_PRICE_USD", 0.023)
MIN_BGM_PURCHASE: int  = _int("MIN_BGM_PURCHASE", 10)
# Crypto via OxaPay (https://oxapay.com) — no-KYC, instant API key, low fees.
# One credential. Webhook: <BOT_PUBLIC_URL>/oxapay-webhook (sent per-invoice).
OXAPAY_MERCHANT_API_KEY: str = os.getenv("OXAPAY_MERCHANT_API_KEY", "")


# ── AI recommendations (Claude) ──────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str   = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


# ── anti-bot captcha ─────────────────────────────────────────────────────────
# Safe in-house emoji-tap captcha (replaces the old token-leaking 3rd-party
# verification). Off by default; enable if you want a bot gate on /start.
CAPTCHA_ENABLED: bool = _bool("CAPTCHA_ENABLED", False)
CAPTCHA_TTL_SECONDS: int = _int("CAPTCHA_TTL_SECONDS", 604800)  # re-verify weekly


# ── startup validation ───────────────────────────────────────────────────────
def validate_runtime_config() -> list[str]:
    """Return a list of fatal problems (empty == good to go)."""
    problems: list[str] = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN is not set.")
    if not MONGO_URLS:
        problems.append("No MongoDB URL set (MONGO_URL / MONGO_URLS / MONGO_URL_1).")
    if not BOT_PUBLIC_URL:
        _log.warning("BOT_PUBLIC_URL not set — Mini Apps (reader/player/games) "
                     "will fall back to in-chat callbacks.")
    return problems
