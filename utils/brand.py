"""
utils/brand.py — single source of truth for the bot's public identity.

The bot is named **Books Provider** (@getfreebooksbot) and is owned, run and
maintained by **@bookslibraryofficial** and **@trinityXmods** and their team.
Every user-facing surface that names the bot or credits its owners pulls from
here, so the branding can never drift out of sync between the chat UI and the
Mini Apps. Internal identifiers (Mongo db name, logger name, the /health
"service" string, backup filenames) deliberately stay "booksbot" — they are not
user-facing and changing them would orphan data / logs.

This module also exposes the shared "premium look" primitives — a divider rule,
a tagline and section-header / footer builders — so the same polished visual
language is reused everywhere instead of being re-invented per handler.
"""
from config import BOT_USERNAME

# Public display name (matches the BotFather title shown on the bot's profile).
BOT_NAME = "Books Provider"

# One-line positioning statement — the bot's promise to the reader.
TAGLINE = "Your private library of eBooks &amp; audiobooks — read, listen, play &amp; earn."

# The owners / maintainers, in one canonical order. Single place to edit.
OWNER_HANDLES = ("@bookslibraryofficial", "@trinityXmods")

# Plain-text join (e.g. for logs / non-HTML contexts).
OWNERS = " & ".join(OWNER_HANDLES)
# HTML-safe join (the bot sends parse_mode=HTML everywhere).
OWNERS_HTML = " &amp; ".join(OWNER_HANDLES)

# Shared horizontal rule — use to separate a header from its body on rich cards.
DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
# A lighter rule for sub-sections inside a single card.
THIN_RULE = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

# Compact credit line for captions / footers (HTML contexts).
CREDIT = f"❤️ <i>Curated &amp; delivered by {OWNERS_HTML}</i>"

# One-line ownership footer for the dashboard.
DASHBOARD_FOOTER = (
    f"<i>👑 Officially owned &amp; operated by {OWNERS_HTML} and their team — "
    "your one true home for free books.</i>"
)


def header(title: str, *, emoji: str = "", subtitle: str = "") -> str:
    """Build a consistent premium card header:

        <emoji> <b>Title</b>
        ────────────────────
        <i>subtitle</i>

    `title`/`subtitle` must already be HTML-safe (escape any dynamic parts before
    passing them in)."""
    top = f"{emoji} ".lstrip() + f"<b>{title}</b>"
    out = f"{top}\n{DIVIDER}"
    if subtitle:
        out += f"\n<i>{subtitle}</i>"
    return out


def about_text() -> str:
    """Full 'About' blurb shown from Bot Tools → ℹ️ About."""
    return (
        f"ℹ️ <b>About {BOT_NAME}</b>\n"
        f"{DIVIDER}\n"
        f"<i>{TAGLINE}</i>\n\n"
        f"<b>{BOT_NAME}</b> (@{BOT_USERNAME}) is a complete reading universe living "
        "inside Telegram — a vast archive of eBooks and audiobooks, a built-in "
        "reader &amp; player, AI-powered discovery, brain games and daily rewards.\n\n"
        "<blockquote>📚 <b>A boundless library</b> — tens of thousands of titles, "
        "instant search &amp; delivery\n"
        "📖 <b>Read anywhere</b> — PDF · EPUB · comics with bookmarks, themes &amp; "
        "read-aloud\n"
        "🎧 <b>Listen on the go</b> — full audiobook player with speed &amp; resume\n"
        "🤖 <b>AI that knows your taste</b> — recommendations, summaries &amp; mood picks\n"
        "🎮 <b>Play &amp; earn</b> — quizzes, word games and daily challenges\n"
        "🎁 <b>Real rewards</b> — claims, spins, quests, referrals &amp; VIP perks</blockquote>\n\n"
        "👑 <b>Owned, run &amp; maintained by</b>\n"
        f"• {OWNER_HANDLES[0]}\n"
        f"• {OWNER_HANDLES[1]}\n"
        "…and their dedicated team.\n\n"
        "Every book, game and feature here is built and operated by them. This is "
        "the one and only official home of the bot — if anyone claims otherwise, "
        "it isn't us.\n\n"
        f"{CREDIT}"
    )
