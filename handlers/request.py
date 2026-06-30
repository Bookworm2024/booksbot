"""
handlers/request.py — Request Center: find & receive files.

Mirrors the TBC bot's "Request via Bot" path:
  Request Center → 🤖 Request Bot → type a title → paginated results →
  tap a result → 1 token deducted (BCN-first) → file delivered from the
  archive channel via copy_message. No match → added to the user's Watchlist.

"Request via Admin" (manual fulfilment) and history are wired as stubs here;
they arrive in the requests phase.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import BOT_PUBLIC_URL
from utils.logs import log_book_found
from database.connection import MongoManager
from utils.brand import CREDIT
from utils.channel import get_file_channel
from utils.files import (_MAX_SCAN, archive_count, fuzzy_search, get_file,
                         icon_for, search)
from utils.format import fmt_amount
from utils.keyboards import btn, kb, webapp_btn

logger = logging.getLogger(__name__)
router = Router()

_PER_PAGE = 8
_RR_PER = 8            # recent-requests rows per page
_RR_WINDOW_DAYS = 7    # show every request from the past 7 days
_RR_MAX = 300          # safety cap on how many we materialise
_DOWNLOAD_COST = 1.0
_NORM_RE = re.compile(r"[^a-z0-9 ]+")


class RequestFSM(StatesGroup):
    awaiting_query = State()


def _norm(text: str) -> str:
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


# ── Request Center ─────────────────────────────────────────────────────────────
def _request_center():
    text = (
        "📚 <b>Request Center</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Two ways to find any title — we'll take it from here.</i>\n\n"
        "<blockquote>"
        "🤖 <b>Request Bot</b> — search our 24/7 archive in an instant and "
        "receive your file on the spot. Fast, fully automatic, always on.\n\n"
        "👤 <b>Request Admin</b> — for the rare and out-of-print titles our "
        "archive doesn't carry yet. A curator sources it for you by hand."
        "</blockquote>\n"
        "<i>💡 Start with the bot — it covers the vast majority of titles.</i>")
    markup = kb(
        [btn("🤖 Request Bot", "req_auto", style="success"),
         btn("👤 Request Admin", "req_manual", style="primary")],
        [btn("📜 My History", "req_history", style="primary")],
        [btn("🔙 Back", "menu_home", style="danger")],
    )
    return text, markup


@router.message(Command("request"))
async def cmd_request(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = _request_center()
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu_request")
async def cb_request_center(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    text, markup = _request_center()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "req_auto")
async def cb_req_auto(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    from utils.flags import is_on
    if not await is_on("search"):
        await call.message.edit_text(
            "🔎 <b>Search is taking a short break</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Our archive is being tuned for you.</i>\n\n"
            "<blockquote>"
            "Instant search is paused for a moment while we polish the index. "
            "Everything else in your library stays open — pop back shortly and "
            "we'll have you covered."
            "</blockquote>",
            reply_markup=kb([btn("🔙 Back", "menu_request", style="danger")]))
        return
    await state.set_state(RequestFSM.awaiting_query)
    await call.message.edit_text(
        "🔍 <b>Search the Archive</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Type a title — your file is moments away.</i>\n\n"
        "<blockquote>"
        "Send the <b>title</b> or a few keywords for the book or audiobook you "
        "have in mind. Our instant search reads every word, forgives the odd "
        "typo, and surfaces the closest matches in seconds.\n\n"
        "📝 Try something like <code>atomic habits</code>"
        "</blockquote>\n"
        "<i>🕘 Want a title you looked up before? Open your Recent Requests.</i>",
        reply_markup=kb(
            [btn("🕘 Recent Requests", "rr:0", style="primary")],
            [btn("🔙 Back", "menu_request", style="danger")],
        ),
    )


@router.message(RequestFSM.awaiting_query, F.text)
async def on_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if query.startswith("/"):
        await state.clear()
        return
    await state.update_data(sq=query, sp=0)
    await _record_search(message.chat.id, query)
    await _render_results(message, state, query, 0, edit=False)


async def _record_search(uid: int, query: str) -> None:
    """Keep the user's last 8 distinct searches (for the Watchlist opt-in) AND
    append a timestamped entry to `search_log` (powers the paginated Recent
    Requests view of every search over the past 7 days)."""
    q = (query or "").strip()
    if not q:
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid}, {"$pull": {"search_history": q}}, upsert=False)
    await db.safe_update("users", {"user_id": uid},
                         {"$push": {"search_history": {"$each": [q], "$position": 0, "$slice": 8}}},
                         upsert=False)
    await db.safe_insert("search_log",
                         {"user_id": uid, "query": q, "at": datetime.now(timezone.utc)})


async def _recent_requests(uid: int) -> list[str]:
    """Every search this user ran in the past 7 days, newest first (capped)."""
    db = await MongoManager.get()
    since = datetime.now(timezone.utc) - timedelta(days=_RR_WINDOW_DAYS)
    rows = await db.find_global("search_log", {"user_id": uid, "at": {"$gte": since}},
                                sort=[("at", -1)], limit=_RR_MAX)
    return [r.get("query", "") for r in rows if r.get("query")]


# ── shared: turn a title into live archive results ───────────────────────────────
async def find_in_library(message: Message, state: FSMContext, query: str, *,
                          edit: bool = False) -> None:
    """Run `query` against the archive and render the standard results view (or the
    not-found card with a Request-from-a-Curator button). Reused by the AI
    recommendation / summary screens so a tapped title fetches straight from the
    library."""
    await state.update_data(sq=query, sp=0, sf="all", ss="relevance")
    await _render_results(message, state, query, 0, edit=edit)


# ── Recent Requests (paginated, last 7 days) ─────────────────────────────────────
@router.callback_query(F.data.startswith("rr:"))
async def cb_recent_requests(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    try:
        page = max(0, int(call.data.split(":", 1)[1]))
    except ValueError:
        page = 0
    queries = await _recent_requests(call.from_user.id)
    if not queries:
        await call.message.edit_text(
            "🕘 <b>Recent Requests</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Every title you've searched in the last 7 days lands here.</i>\n\n"
            "<blockquote>You haven't searched anything in the past week yet. Run a "
            "search and it'll show up here — tap any past request to instantly run "
            "it again.</blockquote>",
            reply_markup=kb([btn("🔍 New Search", "req_auto", style="success")],
                            [btn("🔙 Back", "menu_request", style="danger")]))
        return
    pages = max(1, (len(queries) + _RR_PER - 1) // _RR_PER)
    page = min(page, pages - 1)
    chunk = queries[page * _RR_PER:(page + 1) * _RR_PER]
    rows = [[btn(f"🔁 {q[:42]}", f"rrq:{page}:{i}", style="primary")]
            for i, q in enumerate(chunk)]
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Newer", f"rr:{page-1}", style="primary"))
    if page + 1 < pages:
        nav.append(btn("Older ➡️", f"rr:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔍 New Search", "req_auto", style="success")])
    rows.append([btn("🔙 Back", "menu_request", style="danger")])
    await call.message.edit_text(
        "🕘 <b>Recent Requests</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Everything you've looked up in the past 7 days — newest first.</i>\n\n"
        "<blockquote>Tap any past request to run it again in an instant.</blockquote>\n"
        f"<i>📄 Page <code>{page+1}</code> of <code>{pages}</code> · "
        f"<code>{len(queries)}</code> request(s) this week.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("rrq:"))
async def cb_run_recent(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    try:
        _, page_s, i_s = call.data.split(":", 2)
        page, i = int(page_s), int(i_s)
    except (ValueError, IndexError):
        return
    queries = await _recent_requests(call.from_user.id)
    idx = page * _RR_PER + i
    if idx >= len(queries):
        await call.answer("That request has rolled off your 7-day history — just search the title again.", show_alert=True)
        return
    await find_in_library(call.message, state, queries[idx], edit=True)


@router.callback_query(F.data.in_({"sr_next", "sr_prev"}))
async def cb_page(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    query = data.get("sq", "")
    page = int(data.get("sp", 0)) + (1 if call.data == "sr_next" else -1)
    page = max(0, page)
    await state.update_data(sp=page)
    await _render_results(call.message, state, query, page, edit=True)


_FILTERS = [("all", "All"), ("pdf", "📄 PDF"), ("epub", "📘 EPUB"), ("audio", "🎧 Audio")]
_SORTS = [("relevance", "🎯 Best"), ("new", "🆕 New"), ("popular", "🔥 Popular")]


@router.callback_query(F.data.startswith("sf:"))
async def cb_filter(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    await state.update_data(sf=call.data.split(":", 1)[1], sp=0)
    await _render_results(call.message, state, data.get("sq", ""), 0, edit=True)


@router.callback_query(F.data.startswith("so:"))
async def cb_sort(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    await state.update_data(ss=call.data.split(":", 1)[1], sp=0)
    await _render_results(call.message, state, data.get("sq", ""), 0, edit=True)


async def _render_results(message: Message, state: FSMContext, query: str,
                          page: int, *, edit: bool) -> None:
    data = await state.get_data()
    sf = data.get("sf", "all")
    ss = data.get("ss", "relevance")
    ftype = None if sf == "all" else sf
    results, total = await search(query, skip=page * _PER_PAGE, limit=_PER_PAGE,
                                  ftype=ftype, sort=ss)

    # No exact hits → fall back to a typo-tolerant fuzzy search before giving up.
    fuzzy = False
    if total == 0:
        results, total = await fuzzy_search(query, skip=page * _PER_PAGE,
                                            limit=_PER_PAGE, ftype=ftype)
        fuzzy = total > 0

    if total == 0 and sf == "all":
        # Exit the search flow so the user isn't trapped re-searching every text
        # they type. The query stays in search_history for the opt-in watchlist.
        await state.clear()
        if await archive_count() == 0:
            # the archive itself is empty/unindexed — be honest, don't blame the query
            text = ("📚 <b>Your library is being prepared</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>The shelves are going up as we speak.</i>\n\n"
                    "<blockquote>"
                    "No titles have been indexed yet, so there's nothing to search "
                    "for the moment. Our team is connecting the archive and importing "
                    "the collection now.\n\n"
                    "In the meantime, you can ask a curator to source a specific title "
                    "for you by hand."
                    "</blockquote>\n"
                    "<i>💡 Check back soon — the catalogue grows daily.</i>")
            markup = kb([btn("👤 Request an Admin", "req_manual", style="success")],
                        [btn("🔙 Menu", "menu_home", style="danger")])
        else:
            text = (f"🔍 <b>Not in the library yet</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>We couldn't find <code>{escape(query)}</code> in the archive.</i>\n\n"
                    "<blockquote>"
                    "It's not here right now — but it doesn't have to stay that way:\n\n"
                    "👤 <b>Request an admin</b> to source it for you by hand.\n"
                    "🔔 <b>Get notified</b> the moment it lands in the archive.\n"
                    "🔍 <b>Try another spelling</b> or a few keywords."
                    "</blockquote>")
            markup = kb([btn("👤 Request an Admin", "req_manual", style="success")],
                        [btn("🔔 Notify Me When Added", "wl_last", style="primary")],
                        [btn("🔍 New Search", "req_auto", style="primary")],
                        [btn("🔙 Menu", "menu_home", style="danger")])
        await (message.edit_text if edit else message.answer)(text, reply_markup=markup)
        return

    # Clean up messy archive titles for the button labels (cached; one batched AI
    # call for any uncached titles on this page). Show a brief "Preparing…" card
    # while we tidy the first-seen titles.
    from utils import prepare
    if edit and prepare.has_uncached(results):
        try:
            await message.edit_text("🔄 <b>Preparing your results…</b>\n"
                                    "<i>Tidying up the titles for you.</i>")
        except Exception:  # noqa: BLE001
            pass
    clean_map = await prepare.clean_names_for(results)

    rows = []
    # filter row (active filter highlighted)
    rows.append([btn(("● " if v == sf else "") + lbl, f"sf:{v}",
                     style="success" if v == sf else "primary") for v, lbl in _FILTERS])
    # sort row (exact search only — fuzzy results are similarity-ranked)
    if not fuzzy:
        rows.append([btn(("● " if v == ss else "") + lbl, f"so:{v}",
                         style="success" if v == ss else "primary") for v, lbl in _SORTS])
    for f in results:
        fuid = f["file_unique_id"]
        clean = clean_map.get(fuid) or f.get("name", "Untitled")
        label = f"{icon_for(f.get('ext',''))} {clean[:38]}"
        rows.append([btn(label, f"dl:{fuid}", style="success")])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", "sr_prev", style="primary"))
    # Only the first _MAX_SCAN matches are materialised, so never offer a Next that
    # would page into an empty void (rows[skip:] is empty once skip ≥ _MAX_SCAN).
    reachable = min(total, _MAX_SCAN)
    if (page + 1) * _PER_PAGE < reachable:
        nav.append(btn("Next ➡️", "sr_next", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔍 New Search", "req_auto", style="primary"),
                 btn("🔙 Menu", "menu_home", style="danger")])

    pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    head = (f"🔎 <b>Closest matches for</b> <code>{escape(query)}</code>" if fuzzy
            else f"🔍 <b>Results for</b> <code>{escape(query)}</code>")
    hint = "\n<i>No exact title — here are the nearest reads we found.</i>" if fuzzy else ""
    text = (f"{head}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <code>{total}</code> match(es) · page <code>{page + 1}/{pages}</code>{hint}\n\n"
            "<blockquote>"
            "📥 Tap any title below and it's delivered to your chat instantly.\n\n"
            "💸 <b>Delivery</b> — free with your daily quota. "
            "<i>👑 Premium reads unlimited; past the free limit a single file is a small wallet charge.</i>"
            "</blockquote>")
    await (message.edit_text if edit else message.answer)(text, reply_markup=kb(*rows))


async def _add_watchlist(user_id: int, query: str) -> None:
    db = await MongoManager.get()
    await db.safe_update(
        "watchlist", {"user_id": user_id, "query_norm": _norm(query)},
        {"$set": {"user_id": user_id, "query": query, "query_norm": _norm(query),
                  "matched": False, "created_at": datetime.now(timezone.utc)}},
    )


@router.callback_query(F.data == "wl_last")
async def cb_watch_last(call: CallbackQuery) -> None:
    """Opt-in: add the user's most recent search to their watchlist on demand
    (instead of auto-adding every no-match, which spammed the list)."""
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": call.from_user.id},
                                 {"search_history": 1}) or {}
    hist = u.get("search_history") or []
    if not hist:
        await call.answer("Run a search first — then I can keep an eye out for that title.", show_alert=True)
        return
    await _add_watchlist(call.from_user.id, hist[0])
    await call.answer("🔔 Done — I'll message you the moment it lands in the archive.", show_alert=True)
    await call.message.edit_text(
        "🔔 <b>Added to your Watchlist</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Consider it tracked — we'll do the watching for you.</i>\n\n"
        "<blockquote>"
        f"We're now keeping an eye out for <code>{escape(hist[0])}</code>. "
        "The moment it lands in the archive, we'll send it straight to your inbox — "
        "no need to check back."
        "</blockquote>",
        reply_markup=kb([btn("🔍 New Search", "req_auto", style="primary")],
                        [btn("🔙 Menu", "menu_home", style="danger")]))


# ── Download / delivery ─────────────────────────────────────────────────────────
# Freemium: archive deliveries are gated by a 24h quota (utils.quota key "dl"),
# NOT a token cost. PREMIUM = unlimited; FREE = 2/day, then a per-file wallet
# overage (₹100 / $2). Favorites & Finished re-fetches use their own handlers and
# never touch this quota.
def _file_buttons(fuid: str, f: dict):
    ext = (f.get("ext") or "").lower()
    is_audio = f.get("kind") == "audio"
    rows = []
    # Read/Listen opens the universal reader Mini App (routes by type). Shown only
    # when a Mini-App host is configured.
    if BOT_PUBLIC_URL:
        rows.append([webapp_btn(
            "🎧 Listen Now" if is_audio else "📖 Open in Reader",
            "view.html", query=f"fuid={fuid}&ext={ext}", style="success")])
    rows.append([btn("⭐ Save to Favorites", f"fav_add:{fuid}", style="success")])
    return kb(*rows)


async def _deliver_file(call: CallbackQuery, uid: int, f: dict, fuid: str, *, tag: str) -> bool:
    """Brand + deliver the file (utils.prepare handles the cover/clean-name/"Preparing"
    UX) and do all success bookkeeping. Returns whether it was delivered. The CALLER
    owns refund-on-failure (quota or wallet)."""
    from utils import prepare
    delivered = await prepare.deliver(
        call.bot, uid, f, reply_markup=_file_buttons(fuid, f),
        note="<i>Delivered to your library — enjoy the read.</i>")
    if not delivered:
        return False

    # success bookkeeping
    db = await MongoManager.get()
    now = datetime.now(timezone.utc)
    year = now.year
    await db.safe_update("users", {"user_id": uid},
                         {"$inc": {"downloads": 1, f"reads.{year}": 1}})
    # record in the user's library so the reader/player Mini App is authorized to
    # stream it (the /api/file endpoint gates on favorites OR library).
    await db.safe_update(
        "library", {"user_id": uid, "file_unique_id": fuid},
        {"$set": {"user_id": uid, "file_unique_id": fuid, "name": f.get("name"),
                  "ext": f.get("ext"), "kind": f.get("kind"), "chan_id": f.get("chan_id"),
                  "msg_id": f.get("msg_id"), "file_id": f.get("file_id"), "at": now}},
        upsert=True)
    # downloading a book clears it from the Reading List (TBR)
    for idx in db.healthy:
        await db.dbs[idx]["tbr"].delete_one({"user_id": uid, "file_unique_id": fuid})
    from utils.files import bump_download
    await bump_download(fuid)
    from utils.missions import mark
    await mark(uid, "download")
    # track reading taste by genre (AI-driven) → powers the 🎯 For You shelf.
    import asyncio
    from utils.foryou import record_genre_read
    asyncio.create_task(record_genre_read(uid, f.get("name") or "", file_doc=f, fuid=fuid))
    # nudge the next volume if this title is part of a detected series
    try:
        from utils.series import next_volume
        nxt = await next_volume(f)
        if nxt:
            await call.message.answer(
                "📚 <b>Next in the series</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Keep the story going — the next volume is ready for you.</i>\n\n"
                f"<blockquote>📖 {escape(nxt.get('name') or '')}</blockquote>",
                reply_markup=kb([btn(f"📥 Get «{(nxt.get('name') or '')[:28]}»",
                                     f"dl:{nxt['file_unique_id']}", style="success")]))
    except Exception:  # noqa: BLE001 — a nudge must never break delivery
        pass
    # found-a-book → admin (full detail) + public (privacy-safe) activity log
    await log_book_found(call.bot, uid, f.get("name") or "", f.get("ext") or "", tag)
    return True


async def _delivery_failed(call: CallbackQuery, note: str = "") -> None:
    await call.message.answer(
        "⚠️ <b>That delivery didn't go through</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        + (f"<i>{note}</i>\n\n" if note else "")
        + "<blockquote>"
        "We couldn't hand over this file just now. The copy may have been removed "
        "from the archive, or we've briefly lost access to the source channel.\n\n"
        "Please try another result, or run a fresh search — we'll find you another copy."
        "</blockquote>")


async def _user_currency(uid: int) -> str:
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid}, {"currency": 1}) or {}
    return (u.get("currency") or "USD").upper()


async def _overage_options(uid: int) -> list:
    """(bucket, price, symbol) options for per-file overage, the user's preferred
    currency first."""
    from utils.premium import overage_inr, overage_usd
    opts = [("wallet_inr", await overage_inr(), "₹"),
            ("wallet_usd", await overage_usd(), "$")]
    if (await _user_currency(uid)) != "INR":
        opts = opts[::-1]
    return opts


@router.callback_query(F.data.startswith("dl:"))
async def cb_download(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("This title is no longer in the archive. Try a fresh search and we'll find another copy.", show_alert=True)
        return

    from utils import premium, quota
    consumed = False
    if await premium.is_premium(uid):
        await quota.consume(uid, "dl")  # unlimited; recorded for stats only
        tag = "PREMIUM"
    elif await quota.consume(uid, "dl"):
        consumed = True
        tag = "FREE"
    else:
        # free quota exhausted → offer the paid per-file overage instead of stopping
        await call.answer()
        await _offer_overage(call, fuid, f)
        return

    await call.answer("📤 Delivering your title — one moment…")
    if not await _deliver_file(call, uid, f, fuid, tag=tag):
        if consumed:
            await quota.refund_one(uid, "dl")  # a failed delivery never burns a free download
        await _delivery_failed(call, "Your free download wasn't used." if consumed else "")


async def _offer_overage(call: CallbackQuery, fuid: str, f: dict) -> None:
    uid = call.from_user.id
    from utils import quota
    _, lim = await quota.status(uid, "dl")
    bucket, price, sym = (await _overage_options(uid))[0]
    name = escape((f.get("name") or "this title")[:48])
    await call.message.answer(
        "📥 <b>Daily free downloads used up</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>You've delivered your {quota.fmt_limit(lim)} free files today — nice reading.</i>\n\n"
        "<blockquote>"
        f"📖 <b>{name}</b>\n\n"
        f"Grab this one now for <b>{sym}{fmt_amount(price)}</b> from your wallet, or go "
        "👑 <b>Premium</b> for unlimited downloads and the whole library.</blockquote>",
        reply_markup=kb(
            [btn(f"📥 Get it for {sym}{fmt_amount(price)}", f"dlpay:{fuid}", style="success")],
            [btn("👑 Go Premium (unlimited)", "go_premium", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")]))


@router.callback_query(F.data.startswith("dlpay:"))
async def cb_download_paid(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("This title is no longer in the archive. Try a fresh search.", show_alert=True)
        return
    from utils.wallet import spend_money, add_money
    # Charge exactly the option we showed the user (their preferred currency), so
    # the amount debited always matches the price on the button.
    bucket, price, sym = (await _overage_options(uid))[0]
    if not await spend_money(uid, bucket, price):
        await call.answer()
        await call.message.answer(
            "💳 <b>Top up to grab this file</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>A single extra download is {sym}{fmt_amount(price)} — your wallet's a little short.</i>\n\n"
            "<blockquote>Top up your wallet and tap the file again, or go 👑 <b>Premium</b> for "
            "unlimited downloads and skip per-file charges entirely.</blockquote>",
            reply_markup=kb([btn("💳 Top Up Wallet", "acc_buy", style="success")],
                            [btn("👑 Go Premium", "go_premium", style="primary")],
                            [btn("🔙 Back", "menu_home", style="danger")]))
        return
    await call.answer("📤 Delivering your title — one moment…")
    if not await _deliver_file(call, uid, f, fuid, tag=f"PAID {sym}{fmt_amount(price)}"):
        await add_money(uid, bucket, price)  # refund the overage on failure
        await _delivery_failed(call, f"Your {sym}{fmt_amount(price)} was refunded.")
        return
    await call.message.answer(
        f"✅ <b>{sym}{fmt_amount(price)} charged</b> from your wallet for this file — enjoy!\n"
        "<i>💡 👑 Premium makes downloads unlimited, no per-file charge.</i>",
        reply_markup=kb([btn("👑 Go Premium", "go_premium", style="primary")]))


# NOTE: req_manual and req_history are handled by requests_manual.py and track.py.
# Do NOT add stub handlers for them here — request.router is included before those,
# so a stub would shadow the real, working features.
