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
        kb([btn("⭐ Featured", "disc_feat", style="success"),
            btn("🏷 Genres", "disc_genres", style="success")],
           [btn("📚 Collections", "disc_collections", style="success"),
            btn("🖊 Authors", "disc_authors", style="success")],
           [btn("🆕 New Arrivals", "disc_new:0", style="success"),
            btn("🔥 Popular", "disc_pop:0", style="success")],
           [btn("🔗 Series Finder", "disc_series", style="primary"),
            btn("📅 Book of the Day", "disc_botd", style="primary")],
           [btn("💬 Daily Quote", "disc_quote", style="primary"),
            btn("🎯 Challenges", "menu_challenges", style="primary")],
           [btn("🔙 Back", "menu_library", style="danger")]))


@router.callback_query(F.data == "disc_genres")
async def cb_genres(call: CallbackQuery) -> None:
    await call.answer()
    from utils.files import GENRES
    rows, row = [], []
    for g in GENRES:
        row.append(btn(g, f"disc_g:{g}", style="primary"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text("🏷 <b>Browse by Genre</b>\nPick a genre:", reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("disc_g:"))
async def cb_genre_files(call: CallbackQuery) -> None:
    await call.answer()
    from utils.files import files_by_genre
    genre = call.data.split(":", 1)[1]
    items = await files_by_genre(genre, limit=20)
    if not items:
        await call.message.edit_text(
            f"🏷 <b>{genre}</b>\nNo books tagged here yet.",
            reply_markup=kb([btn("🔙 Genres", "disc_genres", style="danger")]))
        return
    rows = [[btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Genres", "disc_genres", style="danger")])
    await call.message.edit_text(
        f"🏷 <b>{genre}</b> · 1 BCN/BGM each", reply_markup=kb(*rows))


@router.callback_query(F.data == "disc_feat")
async def cb_featured(call: CallbackQuery) -> None:
    await call.answer()
    from utils.featured import featured_files
    items = await featured_files(limit=10)
    if not items:
        await call.message.edit_text(
            "⭐ <b>No featured books right now.</b>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    rows = [[btn(f"⭐ {icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text(
        "⭐ <b>Featured Books</b>\n━━━━━━━━━━━━━━━━━━\nHand-picked &amp; sponsored picks:",
        reply_markup=kb(*rows))


# ── Curated collections (keyword shelves over the live archive) ────────────────
_COLLECTIONS = [
    ("🏆", "Award Winners", ["pulitzer", "booker", "nobel", "hugo", "award", "prize"]),
    ("🧛", "Spooky Reads", ["horror", "ghost", "vampire", "haunted", "dracula", "zombie", "witch"]),
    ("🚀", "Space & Sci-Fi", ["space", "mars", "galaxy", "star", "robot", "alien", "cyber", "future"]),
    ("🕵️", "Mystery & Crime", ["murder", "detective", "mystery", "crime", "sherlock", "thief", "spy"]),
    ("💘", "Love & Romance", ["romance", "love story", "wedding", "bride", "heart"]),
    ("👑", "Timeless Classics", ["classic", "tolstoy", "dickens", "austen", "shakespeare", "homer"]),
    ("🧠", "Self-Improvement", ["habit", "mindset", "productivity", "atomic", "success", "discipline"]),
    ("🐉", "Fantasy Epics", ["dragon", "magic", "kingdom", "sword", "wizard", "throne", "quest"]),
    ("📈", "Business & Money", ["business", "money", "invest", "startup", "finance", "wealth"]),
    ("🧒", "Kids & Young Readers", ["children", "junior", "fairy", "disney", "picture book"]),
]


@router.callback_query(F.data == "disc_collections")
async def cb_collections(call: CallbackQuery) -> None:
    await call.answer()
    rows, row = [], []
    for i, (emoji, name, _terms) in enumerate(_COLLECTIONS):
        row.append(btn(f"{emoji} {name}", f"disc_c:{i}", style="primary"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text(
        "📚 <b>Curated Collections</b>\n━━━━━━━━━━━━━━━━━━\nThemed shelves from the archive:",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("disc_c:"))
async def cb_collection_files(call: CallbackQuery) -> None:
    await call.answer()
    from utils.files import search_any
    try:
        i = int(call.data.split(":", 1)[1])
        emoji, name, terms = _COLLECTIONS[i]
    except (ValueError, IndexError):
        await call.answer(); return
    items = await search_any(terms, limit=20)
    if not items:
        await call.message.edit_text(
            f"{emoji} <b>{name}</b>\nNothing here yet — try Genres or search.",
            reply_markup=kb([btn("🔙 Collections", "disc_collections", style="danger")]))
        return
    rows = [[btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Collections", "disc_collections", style="danger")])
    await call.message.edit_text(
        f"{emoji} <b>{name}</b> · 1 BCN/BGM each", reply_markup=kb(*rows))


# ── Author spotlight ───────────────────────────────────────────────────────────
_AUTHORS = [
    ("J.K. Rowling", "Creator of the Harry Potter wizarding world.", ["rowling", "harry potter"]),
    ("J.R.R. Tolkien", "Father of modern fantasy — Middle-earth.", ["tolkien", "lord of the rings", "hobbit"]),
    ("Stephen King", "The reigning king of horror & suspense.", ["stephen king"]),
    ("Agatha Christie", "Best-selling mystery novelist of all time.", ["agatha christie", "poirot"]),
    ("George Orwell", "Dystopian visionary — 1984 & Animal Farm.", ["orwell", "1984", "animal farm"]),
    ("Jane Austen", "Wit & romance of Regency England.", ["austen", "pride and prejudice"]),
    ("Brandon Sanderson", "Epic fantasy & the Cosmere.", ["sanderson", "mistborn", "stormlight"]),
    ("Dan Brown", "Symbology thrillers — Robert Langdon.", ["dan brown", "da vinci"]),
    ("Paulo Coelho", "Inspirational fiction — The Alchemist.", ["coelho", "alchemist"]),
    ("Haruki Murakami", "Surreal modern literary fiction.", ["murakami"]),
    ("Ernest Hemingway", "Spare, powerful 20th-century prose.", ["hemingway"]),
    ("Isaac Asimov", "Grandmaster of science fiction.", ["asimov", "foundation"]),
    ("Roald Dahl", "Beloved children's storyteller.", ["roald dahl", "dahl", "matilda"]),
    ("Mark Twain", "The great American humorist.", ["mark twain", "tom sawyer", "huckleberry"]),
    ("Charles Dickens", "Victorian social novelist.", ["dickens"]),
    ("Rick Riordan", "Mythology-fueled YA adventures.", ["riordan", "percy jackson"]),
]


@router.callback_query(F.data == "disc_authors")
async def cb_authors(call: CallbackQuery) -> None:
    await call.answer()
    aotd = _day_index() % len(_AUTHORS)
    rows, row = [], []
    for i, (name, _b, _t) in enumerate(_AUTHORS):
        label = ("⭐ " if i == aotd else "") + name
        row.append(btn(label, f"disc_a:{i}", style="primary"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text(
        "🖊 <b>Author Spotlight</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"⭐ <b>Author of the Day:</b> {_AUTHORS[aotd][0]}\n\nPick an author to explore:",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("disc_a:"))
async def cb_author_files(call: CallbackQuery) -> None:
    await call.answer()
    from utils.files import search_any
    try:
        i = int(call.data.split(":", 1)[1])
        name, blurb, terms = _AUTHORS[i]
    except (ValueError, IndexError):
        await call.answer(); return
    items = await search_any(terms, limit=16)
    head = f"🖊 <b>{name}</b>\n<i>{blurb}</i>\n━━━━━━━━━━━━━━━━━━\n"
    if not items:
        await call.message.edit_text(
            head + "No titles in the archive yet — try 📚 Request to ask for one.",
            reply_markup=kb([btn("📚 Request a Book", "menu_request", style="success")],
                            [btn("🔙 Authors", "disc_authors", style="danger")]))
        return
    rows = [[btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Authors", "disc_authors", style="danger")])
    await call.message.edit_text(head + "Their books in the archive:", reply_markup=kb(*rows))


# ── Series finder ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "disc_series")
async def cb_series(call: CallbackQuery) -> None:
    await call.answer()
    from utils.series import parse_series
    pool = (await recent_files(limit=200)) + (await popular_files(limit=200))
    # group by normalized series base; keep the lowest-volume file as representative
    groups: dict[str, dict] = {}
    seen_fuid = set()
    for f in pool:
        if f.get("file_unique_id") in seen_fuid:
            continue
        seen_fuid.add(f.get("file_unique_id"))
        parsed = parse_series(f.get("name", ""))
        if not parsed:
            continue
        base, num = parsed
        key = "".join(ch for ch in base.lower() if ch.isalnum())
        if not key:
            continue
        g = groups.setdefault(key, {"base": base, "nums": set(), "rep": f, "rep_num": num})
        g["nums"].add(num)
        if num < g["rep_num"]:
            g["rep"], g["rep_num"] = f, num
    series = [g for g in groups.values() if len(g["nums"]) >= 2]
    series.sort(key=lambda g: len(g["nums"]), reverse=True)
    if not series:
        await call.message.edit_text(
            "🔗 <b>Series Finder</b>\n━━━━━━━━━━━━━━━━━━\n"
            "No multi-volume series detected in the archive yet.\n"
            "<i>Tip: after any download I'll suggest the next volume automatically.</i>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    rows = [[btn(f"📚 {g['base'][:30]} ({len(g['nums'])} vol)",
                 f"disc_sr:{g['rep']['file_unique_id']}", style="primary")]
            for g in series[:12]]
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text(
        "🔗 <b>Series Finder</b>\n━━━━━━━━━━━━━━━━━━\nMulti-volume series in the archive:",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("disc_sr:"))
async def cb_series_detail(call: CallbackQuery) -> None:
    await call.answer()
    from utils.files import get_file
    from utils.series import find_series, parse_series
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.message.edit_text("That title is no longer available.",
                                     reply_markup=kb([btn("🔙 Series", "disc_series", style="danger")]))
        return
    vols = await find_series(f)
    parsed = parse_series(f.get("name", ""))
    base = parsed[0] if parsed else f.get("name", "Series")
    if not vols:
        vols = [f]
    rows = []
    for v in vols:
        vp = parse_series(v.get("name", ""))
        tag = f"#{vp[1]} " if vp else ""
        rows.append([btn(f"📥 {tag}{v.get('name','Untitled')[:32]}",
                         f"dl:{v['file_unique_id']}", style="success")])
    rows.append([btn("🔙 Series", "disc_series", style="danger")])
    await call.message.edit_text(
        f"📚 <b>{base}</b> · {len(vols)} volume(s)\n━━━━━━━━━━━━━━━━━━\nRead them in order:",
        reply_markup=kb(*rows))


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
