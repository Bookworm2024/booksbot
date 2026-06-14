"""
handlers/inline.py — inline-mode archive search.

Type `@yourbot atomic habits` in ANY chat → matching titles appear. Picking one
posts a card that deep-links back into the bot (`?start=dl_<fuid>`), where the
normal token-gated download happens. This drives users into the bot rather than
leaking files for free, and turns every chat into a discovery surface.

Requires enabling inline mode once in @BotFather (/setinline).
"""
import logging

from aiogram import F, Router
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from config import BOT_USERNAME
from utils.files import icon_for, search

logger = logging.getLogger(__name__)
router = Router()


@router.inline_query(F.query.len() > 0)
async def inline_search(iq: InlineQuery) -> None:
    q = (iq.query or "").strip()
    results, _ = await search(q, limit=20)
    articles = []
    for i, f in enumerate(results):
        fuid = f["file_unique_id"]
        name = f.get("name", "Untitled")
        ext = (f.get("ext") or "").upper()
        link = f"https://t.me/{BOT_USERNAME}?start=dl_{fuid}"
        articles.append(InlineQueryResultArticle(
            id=str(i),
            title=f"{icon_for(f.get('ext',''))} {name[:60]}",
            description=f".{ext} · tap to get it in the bot",
            input_message_content=InputTextMessageContent(
                message_text=f"📚 <b>{name}</b>\n📥 Get it on @{BOT_USERNAME}",
                parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📥 Get this book", url=link)]]),
        ))
    await iq.answer(articles, cache_time=10, is_personal=True,
                    switch_pm_text="🔍 Open BooksBot", switch_pm_parameter="start")


@router.inline_query()  # empty query → prompt
async def inline_empty(iq: InlineQuery) -> None:
    await iq.answer([], cache_time=5, is_personal=True,
                    switch_pm_text="Type a book title to search…",
                    switch_pm_parameter="start")
