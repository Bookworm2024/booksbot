"""
handlers/discover.py — Discovery hub (Pillar 2).

My Library → 🔭 Discover:
  🆕 New Arrivals   — newest files added to the archive
  🔥 Popular        — most-downloaded titles (all-time)
  📅 Book of the Day — a deterministic daily pick
  💬 Daily Quote    — a rotating literary quote

Tapping a title routes through the normal token-gated download (dl:<fuid>).
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.files import book_of_the_day, icon_for, popular_files, recent_files
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_PER = 8

_QUOTES = [
    ("A reader lives a thousand lives before he dies.", "George R.R. Martin"),
    ("So many books, so little time.", "Frank Zappa"),
    ("A room without books is like a body without a soul.", "Cicero"),
    ("Until I feared I would lose it, I never loved to read. One does not love breathing.", "Harper Lee"),
    ("Books are a uniquely portable magic.", "Stephen King"),
    ("The only thing you absolutely have to know is the location of the library.", "Albert Einstein"),
    ("I have always imagined that Paradise will be a kind of library.", "Jorge Luis Borges"),
    ("Reading is to the mind what exercise is to the body.", "Joseph Addison"),
    ("There is no friend as loyal as a book.", "Ernest Hemingway"),
    ("Once you learn to read, you will be forever free.", "Frederick Douglass"),
    ("Books fall open, you fall in.", "David T.W. McCord"),
    ("That's the thing about books. They let you travel without moving your feet.", "Jhumpa Lahiri"),
    ("We read to know we are not alone.", "C.S. Lewis"),
    ("A book is a dream that you hold in your hand.", "Neil Gaiman"),
    ("Sleep is good, he said, and books are better.", "George R.R. Martin"),
]


def _day_index() -> int:
    return (datetime.now(timezone.utc).date() - datetime(2020, 1, 1, tzinfo=timezone.utc).date()).days


@router.message(Command("discover"))
async def cmd_discover(message: Message) -> None:
    await message.answer(*_hub())


@router.callback_query(F.data == "lib_discover")
async def cb_discover(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = _hub()
    await call.message.edit_text(text, reply_markup=markup)


def _hub():
    return (
        "<b>🔭 Discover</b>\n━━━━━━━━━━━━━━━━━━\nFind your next read.",
        kb([btn("🆕 New Arrivals", "disc_new:0", style="success"),
            btn("🔥 Popular", "disc_pop:0", style="success")],
           [btn("📅 Book of the Day", "disc_botd", style="primary"),
            btn("💬 Daily Quote", "disc_quote", style="primary")],
           [btn("🔙 Back", "menu_library", style="danger")]))


def _file_rows(items, page, total, base):
    rows = []
    for f in items:
        rows.append([btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:38]}",
                         f"dl:{f['file_unique_id']}", style="success")])
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", f"{base}:{page-1}", style="primary"))
    if (page + 1) * _PER < total:
        nav.append(btn("Next ➡️", f"{base}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    return rows


@router.callback_query(F.data.startswith("disc_new:"))
async def cb_new(call: CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":", 1)[1])
    items = await recent_files(limit=48)
    if not items:
        await call.message.edit_text("🆕 No files indexed yet.",
                                     reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    chunk = items[page * _PER:(page + 1) * _PER]
    await call.message.edit_text(
        f"🆕 <b>New Arrivals</b> · 1 BCN/BGM each\nPage {page+1}",
        reply_markup=kb(*_file_rows(chunk, page, len(items), "disc_new")))


@router.callback_query(F.data.startswith("disc_pop:"))
async def cb_pop(call: CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":", 1)[1])
    items = await popular_files(limit=48)
    if not items:
        await call.message.edit_text("🔥 No downloads yet — be the first!",
                                     reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    chunk = items[page * _PER:(page + 1) * _PER]
    await call.message.edit_text(
        f"🔥 <b>Popular</b> · most downloaded\nPage {page+1}",
        reply_markup=kb(*_file_rows(chunk, page, len(items), "disc_pop")))


@router.callback_query(F.data == "disc_botd")
async def cb_botd(call: CallbackQuery) -> None:
    await call.answer()
    f = await book_of_the_day(_day_index())
    if not f:
        await call.message.edit_text("📅 No book to feature yet.",
                                     reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    await call.message.edit_text(
        f"📅 <b>Book of the Day</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"{icon_for(f.get('ext',''))} <b>{f.get('name','Untitled')}</b>",
        reply_markup=kb([btn("📥 Get it (1 token)", f"dl:{f['file_unique_id']}", style="success")],
                        [btn("🔙 Discover", "lib_discover", style="danger")]))


@router.callback_query(F.data == "disc_quote")
async def cb_quote(call: CallbackQuery) -> None:
    await call.answer()
    quote, author = _QUOTES[_day_index() % len(_QUOTES)]
    await call.message.edit_text(
        f"💬 <b>Quote of the Day</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"<i>“{quote}”</i>\n\n— <b>{author}</b>",
        reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
