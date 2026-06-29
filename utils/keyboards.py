"""
utils/keyboards.py — coloured keyboard helpers.

House rule: the bot uses COLOURED keyboards everywhere. Every button is built
through these helpers so the styling is consistent and applied in exactly one
place.

Styling uses aiogram 3.25's InlineKeyboardButton.style field (Bot API 9.x):
    success — positive / primary actions (Request, Play, Confirm, Proceed)
    primary — informational / navigation (View, Library, Back, Home)
    danger  — destructive / negative (Cancel, Delete, Ban)

If the deployment talks to the vanilla Telegram Bot API (which ignores/rejects
the style field), set COLORED_BUTTONS=False and strip_styles() removes them
right before send — labels/emoji stay, so nothing else changes.
"""
from typing import Optional

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from config import BOT_PUBLIC_URL, COLORED_BUTTONS

# default style by intent keyword found in the label (lowercased)
_DANGER_HINTS = ("cancel", "delete", "remove", "ban", "decline", "reject", "close", "end")
_SUCCESS_HINTS = ("request", "play", "buy", "confirm", "approve", "proceed", "claim",
                  "redeem", "start", "submit", "add", "pay", "send", "open")


def _auto_style(label: str) -> str:
    low = label.lower()
    if any(h in low for h in _DANGER_HINTS):
        return "danger"
    if any(h in low for h in _SUCCESS_HINTS):
        return "success"
    return "primary"


def btn(label: str, callback: str, *, style: Optional[str] = None) -> InlineKeyboardButton:
    """A coloured callback button."""
    kw = {"text": label, "callback_data": callback}
    if COLORED_BUTTONS:
        kw["style"] = style or _auto_style(label)
    return InlineKeyboardButton(**kw)


def url_btn(label: str, url: str, *, style: str = "primary") -> InlineKeyboardButton:
    kw = {"text": label, "url": url}
    if COLORED_BUTTONS:
        kw["style"] = style
    return InlineKeyboardButton(**kw)


def webapp_btn(label: str, page: str, *, query: str = "",
               style: str = "success", fallback_cb: str = "noop") -> InlineKeyboardButton:
    """Open a Mini App page from web_app/ when BOT_PUBLIC_URL is set (HTTPS),
    else fall back to an in-chat callback so a button is always produced."""
    base = (BOT_PUBLIC_URL or "").rstrip("/")
    if base:
        url = f"{base}/app/{page}".replace("http://", "https://")
        if query:
            url = f"{url}?{query}"
        kw = {"text": label, "web_app": WebAppInfo(url=url)}
        if COLORED_BUTTONS:
            kw["style"] = style
        return InlineKeyboardButton(**kw)
    return btn(label, fallback_cb, style=style)


def cancel_btn(target: str = "menu_home", label: str = "❌ Cancel") -> InlineKeyboardButton:
    """A universal Cancel button. House rule: the bot NEVER asks a user to type a
    command like /cancel — every flow offers this button instead. Tapping it is
    handled globally (handlers.start.cb_flow_cancel): it clears the FSM state and
    shows a tidy 'Cancelled' card with a one-tap link back to `target`
    (e.g. 'menu_home', 'menu_request', 'admin_open', 'admin_ai')."""
    return btn(label, f"flow_cancel:{target}", style="danger")


def cancel_row(target: str = "menu_home", label: str = "❌ Cancel") -> list[InlineKeyboardButton]:
    """A single-button row holding the universal Cancel button — drop it into any
    prompt keyboard so a flow can always be backed out of with a tap."""
    return [cancel_btn(target, label)]


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Assemble rows into a coloured inline keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def strip_styles(markup: Optional[InlineKeyboardMarkup]) -> Optional[InlineKeyboardMarkup]:
    """Remove `style` from every button — used by the send wrapper when the
    target API server doesn't understand coloured buttons."""
    if markup is None:
        return None
    for row in markup.inline_keyboard:
        for b in row:
            if getattr(b, "style", None) is not None:
                b.style = None
    return markup
