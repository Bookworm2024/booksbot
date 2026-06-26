"""
utils/brand.py — single source of truth for the bot's public identity.

The bot is named **Books Provider** (@getfreebooksbot) and is owned, run and
maintained by **@bookslibraryofficial** and **@trinityXmods** and their team.
Every user-facing surface that names the bot or credits its owners pulls from
here, so the branding can never drift out of sync between the chat UI and the
Mini Apps. Internal identifiers (Mongo db name, logger name, the /health
"service" string, backup filenames) deliberately stay "booksbot" — they are not
user-facing and changing them would orphan data / logs.
"""
from config import BOT_USERNAME

# Public display name (matches the BotFather title shown on the bot's profile).
BOT_NAME = "Books Provider"

# The owners / maintainers, in one canonical order. Single place to edit.
OWNER_HANDLES = ("@bookslibraryofficial", "@trinityXmods")

# Plain-text join (e.g. for logs / non-HTML contexts).
OWNERS = " & ".join(OWNER_HANDLES)
# HTML-safe join (the bot sends parse_mode=HTML everywhere).
OWNERS_HTML = " &amp; ".join(OWNER_HANDLES)

# Compact credit line for captions / footers (HTML contexts).
CREDIT = f"❤️ Brought to you by {OWNERS_HTML}"

# One-line ownership footer for the dashboard.
DASHBOARD_FOOTER = f"<i>👑 Owned &amp; managed by {OWNERS_HTML} and their team.</i>"


def about_text() -> str:
    """Full 'About' blurb shown from Bot Tools → ℹ️ About."""
    return (
        f"ℹ️ <b>About {BOT_NAME}</b>\n\n"
        f"<b>{BOT_NAME}</b> (@{BOT_USERNAME}) brings you free eBooks, "
        "audiobooks, a built-in reader &amp; player, reading tools and games — "
        "all inside Telegram.\n\n"
        "👑 <b>Owned, run &amp; maintained by</b>\n"
        f"• {OWNER_HANDLES[0]}\n"
        f"• {OWNER_HANDLES[1]}\n"
        "…and their team.\n\n"
        "Every book, game and feature here is built and operated by them. "
        "This is the only official home of the bot — if anyone claims otherwise, "
        "it isn't us.\n\n"
        f"{CREDIT}"
    )
