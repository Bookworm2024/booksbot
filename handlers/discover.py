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
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils import premium
from utils.files import book_of_the_day, icon_for, popular_files, recent_files
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_PER = 8


def _locked_card(section: str, back_cb: str = "lib_discover"):
    """A consistent Premium-locked card for sections free users can't open."""
    return (
        f"🔒 <b>{section}</b> — <i>Premium</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>👑 <b>{section}</b> is a Premium perk.\n\n"
        "Unlock it — plus unlimited downloads, AI picks and more — by going Premium.</blockquote>",
        kb([btn("👑 Go Premium", "go_premium", style="success")],
           [btn("🔙 Discover", back_cb, style="danger")]))


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
    text, markup = _hub(await premium.is_premium(message.chat.id))
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "lib_discover")
async def cb_discover(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = _hub(await premium.is_premium(call.from_user.id))
    await call.message.edit_text(text, reply_markup=markup)


def _hub(is_prem: bool):
    # New Arrivals, Series Finder and Challenges are Premium-only; free users see
    # them locked (🔒 + "(Premium)") and routed to the shared go_premium upsell.
    if is_prem:
        new_btn = btn("🆕 New Arrivals", "disc_new:0", style="success")
        series_btn = btn("🔗 Series Finder", "disc_series", style="primary")
        chal_btn = btn("🎯 Challenges", "menu_challenges", style="primary")
    else:
        new_btn = btn("🔒 New Arrivals (Premium)", "go_premium", style="primary")
        series_btn = btn("🔒 Series Finder (Premium)", "go_premium", style="primary")
        chal_btn = btn("🔒 Challenges (Premium)", "go_premium", style="primary")
    return (
        "🔭 <b>Discover</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your storefront for the next great read — curated, fresh and waiting.</i>\n"
        "<blockquote>⭐ <b>Featured</b> &amp; 📚 <b>Collections</b> — hand-picked shelves\n"
        "🆕 <b>New Arrivals</b> &amp; 🔥 <b>Popular</b> — what's fresh and what's loved\n"
        "🖊 <b>Authors</b> &amp; 🔗 <b>Series</b> — explore by name, read in order\n"
        "📅 <b>Book of the Day</b> &amp; 💬 <b>Daily Quote</b> — a new spark each morning</blockquote>\n"
        "<i>💡 Tap any shelf below to start browsing — we'll take it from here.</i>",
        kb([btn("⭐ Featured", "disc_feat", style="success"),
            btn("🏷 Genres", "disc_genres", style="success")],
           [btn("📚 Collections", "disc_collections", style="success"),
            btn("🖊 Authors", "disc_authors", style="success")],
           [new_btn,
            btn("🔥 Popular", "disc_pop:0", style="success")],
           [series_btn,
            btn("📅 Book of the Day", "disc_botd", style="primary")],
           [btn("💬 Daily Quote", "disc_quote", style="primary"),
            chal_btn],
           [btn("🔙 Back to Library", "menu_library", style="danger")]))


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
    await call.message.edit_text(
        "🏷 <b>Browse by Genre</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Tell us the mood — we'll line up the shelf.</i>\n"
        "<blockquote>Pick a genre below to see every matching title in the archive.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("disc_g:"))
async def cb_genre_files(call: CallbackQuery) -> None:
    await call.answer()
    from utils.files import files_by_genre
    genre = call.data.split(":", 1)[1]
    items = await files_by_genre(genre, limit=20)
    if not items:
        await call.message.edit_text(
            f"🏷 <b>{genre}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>📭 This shelf is still being stocked — nothing tagged here just yet.\n\n"
            "Try another genre, or search a title and we'll fetch it for you.</blockquote>",
            reply_markup=kb([btn("🔙 Genres", "disc_genres", style="danger")]))
        return
    rows = [[btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Genres", "disc_genres", style="danger")])
    await call.message.edit_text(
        f"🏷 <b>{genre}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Every title on this shelf, ready to read.</i>\n"
        "<blockquote>📥 Tap a cover — delivered free with your daily quota.\n"
        "👑 Premium reads unlimited; past the free limit a single file is a small wallet charge.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data == "disc_feat")
async def cb_featured(call: CallbackQuery) -> None:
    await call.answer()
    from utils.featured import featured_files
    items = await featured_files(limit=10)
    if not items:
        await call.message.edit_text(
            "⭐ <b>Featured</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>🪄 No spotlight titles right now — the marquee is being refreshed.\n\n"
            "Check back soon, or explore 📚 Collections and 🔥 Popular in the meantime.</blockquote>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    rows = [[btn(f"⭐ {icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text(
        "⭐ <b>Featured</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>The marquee — hand-picked and sponsored standouts.</i>\n"
        "<blockquote>These are the titles worth a look first.\n"
        "📥 Tap any to read — free with your daily quota.</blockquote>",
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
        "📚 <b>Curated Collections</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Themed shelves, gathered by hand from across the archive.</i>\n"
        "<blockquote>From award winners to spooky reads to fantasy epics — each shelf "
        "pulls the best matching titles together so you can browse by feeling, not just by name.</blockquote>\n"
        "<i>💡 Pick a shelf to see what's inside.</i>",
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
            f"{emoji} <b>{name}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>📭 This shelf is still filling up — nothing matched just yet.\n\n"
            "Try another collection, browse 🏷 Genres, or search a title directly.</blockquote>",
            reply_markup=kb([btn("🔙 Collections", "disc_collections", style="danger")]))
        return
    rows = [[btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Collections", "disc_collections", style="danger")])
    await call.message.edit_text(
        f"{emoji} <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A shelf curated around this theme — picked just for the mood.</i>\n"
        "<blockquote>📥 Tap any one — delivered free with your daily quota.\n"
        "👑 Premium reads unlimited.</blockquote>",
        reply_markup=kb(*rows))


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
        "🖊 <b>Author Spotlight</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Explore the storytellers behind your favourites.</i>\n"
        f"<blockquote>⭐ <b>Author of the Day:</b> {_AUTHORS[aotd][0]}\n"
        "A fresh name is featured every day — today's is starred below.</blockquote>\n"
        "<i>💡 Tap an author to see their books in the archive.</i>",
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
    head = f"🖊 <b>{name}</b>\n<i>{blurb}</i>\n━━━━━━━━━━━━━━━━━━━━\n"
    if not items:
        await call.message.edit_text(
            head + "<blockquote>📭 None of their titles are in the archive just yet.\n\n"
            "Want one? Tap <b>Request a Book</b> below and our team will hunt it down for you.</blockquote>",
            reply_markup=kb([btn("📚 Request a Book", "menu_request", style="success")],
                            [btn("🔙 Authors", "disc_authors", style="danger")]))
        return
    rows = [[btn(f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:36]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in items]
    rows.append([btn("🔙 Authors", "disc_authors", style="danger")])
    await call.message.edit_text(
        head + "<blockquote>📚 Every title from this author in our archive.\n"
        "📥 Tap any to read — free with your daily quota.</blockquote>",
        reply_markup=kb(*rows))


# ── Series finder ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "disc_series")
async def cb_series(call: CallbackQuery) -> None:
    await call.answer()
    if not await premium.is_premium(call.from_user.id):
        text, markup = _locked_card("Series Finder")
        await call.message.edit_text(text, reply_markup=markup)
        return
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
            "🔗 <b>Series Finder</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>📭 No multi-volume series have surfaced in the archive yet.\n\n"
            "💡 No need to hunt — after any download we'll automatically point you to the next "
            "volume, so a series always keeps flowing.</blockquote>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    rows = [[btn(f"📚 {g['base'][:30]} ({len(g['nums'])} vol)",
                 f"disc_sr:{g['rep']['file_unique_id']}", style="primary")]
            for g in series[:12]]
    rows.append([btn("🔙 Discover", "lib_discover", style="danger")])
    await call.message.edit_text(
        "🔗 <b>Series Finder</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Found a saga you love? Read it in order, start to finish.</i>\n"
        "<blockquote>📚 Every multi-volume series we've spotted in the archive, grouped for you.\n"
        "Tap a series to see all its volumes lined up in sequence.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("disc_sr:"))
async def cb_series_detail(call: CallbackQuery) -> None:
    await call.answer()
    if not await premium.is_premium(call.from_user.id):
        text, markup = _locked_card("Series Finder")
        await call.message.edit_text(text, reply_markup=markup)
        return
    from utils.files import get_file
    from utils.series import find_series, parse_series
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.message.edit_text(
            "⚠️ <b>Title Unavailable</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This title has moved out of the archive and can't be opened right now.\n\n"
            "Head back to <b>Series</b> to pick another saga.</blockquote>",
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
        f"📚 <b>{escape(base)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{len(vols)} volume(s) — the full run, in sequence.</i>\n"
        "<blockquote>📖 Tap each volume in order for the way the story was meant to unfold.\n"
        "📥 Free with your daily quota.</blockquote>",
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
    if not await premium.is_premium(call.from_user.id):
        text, markup = _locked_card("New Arrivals")
        await call.message.edit_text(text, reply_markup=markup)
        return
    page = int(call.data.split(":", 1)[1])
    items = await recent_files(limit=48)
    if not items:
        await call.message.edit_text(
            "🆕 <b>New Arrivals</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>📭 The shelves are still being stocked — nothing new indexed just yet.\n\n"
            "Check back soon, or search a title and we'll fetch it for you.</blockquote>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    chunk = items[page * _PER:(page + 1) * _PER]
    await call.message.edit_text(
        "🆕 <b>New Arrivals</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Hot off the shelf — the freshest additions to the archive.</i>\n"
        f"<blockquote>📥 Delivered free with your daily quota.\n"
        f"📄 Page <code>{page+1}</code> — tap a cover to add it to your library.</blockquote>",
        reply_markup=kb(*_file_rows(chunk, page, len(items), "disc_new")))


@router.callback_query(F.data.startswith("disc_pop:"))
async def cb_pop(call: CallbackQuery) -> None:
    await call.answer()
    page = int(call.data.split(":", 1)[1])
    items = await popular_files(limit=48)
    if not items:
        await call.message.edit_text(
            "🔥 <b>Popular</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>📭 No downloads have landed yet — the charts are wide open.\n\n"
            "Be the first to grab a title and set the trend.</blockquote>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    chunk = items[page * _PER:(page + 1) * _PER]
    await call.message.edit_text(
        "🔥 <b>Popular</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>The crowd favourites — most downloaded of all time.</i>\n"
        f"<blockquote>🏆 If everyone's reading it, there's a reason.\n"
        f"📥 Free with your daily quota · 📄 Page <code>{page+1}</code> — tap to add it to your library.</blockquote>",
        reply_markup=kb(*_file_rows(chunk, page, len(items), "disc_pop")))


@router.callback_query(F.data == "disc_botd")
async def cb_botd(call: CallbackQuery) -> None:
    await call.answer()
    f = await book_of_the_day(_day_index())
    if not f:
        await call.message.edit_text(
            "📅 <b>Book of the Day</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>📭 Today's pick is still being chosen — the spotlight isn't lit yet.\n\n"
            "Check back shortly, or explore 🔥 Popular and ⭐ Featured for a great read now.</blockquote>",
            reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
        return
    await call.message.edit_text(
        "📅 <b>Book of the Day</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>One handpicked read, refreshed every morning — just for today.</i>\n"
        f"<blockquote>{icon_for(f.get('ext',''))} <b>{f.get('name','Untitled')}</b>\n\n"
        "📥 Tap below and it's in your library — free with your daily quota.</blockquote>",
        reply_markup=kb([btn("📥 Claim Today's Pick", f"dl:{f['file_unique_id']}", style="success")],
                        [btn("🔙 Discover", "lib_discover", style="danger")]))


@router.callback_query(F.data == "disc_quote")
async def cb_quote(call: CallbackQuery) -> None:
    await call.answer()
    quote, author = _QUOTES[_day_index() % len(_QUOTES)]
    await call.message.edit_text(
        "💬 <b>Quote of the Day</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A little literary spark to carry into your day.</i>\n"
        f"<blockquote>“{quote}”\n\n— <b>{author}</b></blockquote>\n"
        "<i>💡 A fresh quote lands here every morning.</i>",
        reply_markup=kb([btn("🔙 Discover", "lib_discover", style="danger")]))
