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
from utils.files import archive_count, fuzzy_search, get_file, icon_for, search
from utils.format import fmt_amount
from utils.keyboards import btn, kb, webapp_btn
from utils.wallet import get_balances, refund, spend

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
        label = f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:38]}"
        rows.append([btn(label, f"dl:{fuid}", style="success")])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", "sr_prev", style="primary"))
    if (page + 1) * _PER_PAGE < total:
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
            "💸 <b>Delivery</b> — from <code>1</code> 🪙 BCN / 💎 BGM per title "
            "<i>(less with VIP and during Happy Hour; more during surge).</i>"
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
@router.callback_query(F.data.startswith("dl:"))
async def cb_download(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("This title is no longer in the archive. Try a fresh search and we'll find another copy.", show_alert=True)
        return

    from utils.settings import get_float
    from utils.vip import download_factor
    from utils.pricing import download_multiplier
    cost = round(await get_float("download_cost") * await download_factor(uid)
                 * await download_multiplier(f), 4)

    if cost <= 0:
        currency = "VIP"  # Gold VIP → free downloads
    else:
        bgm, bcn = await get_balances(uid)
        if bgm + bcn < cost:
            await call.answer()
            await call.message.answer(
                "💼 <b>A little short for this one</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>You're almost there — top up and it's yours.</i>\n\n"
                "<blockquote>"
                f"This title costs <code>{fmt_amount(cost)}</code> 🪙 BCN / 💎 BGM to deliver, "
                "and your balance doesn't quite cover it yet.\n\n"
                "⚡ Claim your free daily 🪙 BCN with /claim\n"
                "💎 Buy 💎 BGM for instant, lasting balance\n"
                "👑 Go Premium for cheaper — even free — downloads"
                "</blockquote>",
                reply_markup=kb([btn("💎 Buy BGM", "acc_buy", style="success"),
                                 btn("👑 Go Premium", "acc_vip", style="primary")]),
            )
            return
        currency = await spend(uid, cost)
        if not currency:
            await call.answer("Your balance just changed and no longer covers this title — top up and try again.", show_alert=True)
            return

    await call.answer("📤 Delivering your title — one moment…")
    caption = (f"{icon_for(f.get('ext',''))} <b>{escape(f.get('name','Your File') or 'Your File')}</b>\n"
               "━━━━━━━━━━━━━━━━━━━━\n"
               "<i>Delivered to your library — enjoy the read.</i>\n\n"
               f"{CREDIT}")
    # Read/Listen opens the universal reader Mini App (routes by type: PDF/EPUB →
    # reader, audio → player, etc.). Shown only when a Mini-App host is configured.
    ext = (f.get("ext") or "").lower()
    is_audio = f.get("kind") == "audio"
    fav_rows = []
    if BOT_PUBLIC_URL:
        fav_rows.append([webapp_btn(
            "🎧 Listen Now" if is_audio else "📖 Open in Reader",
            "view.html", query=f"fuid={fuid}&ext={ext}", style="success")])
    fav_rows.append([btn("⭐ Save to Favorites", f"fav_add:{fuid}", style="success")])
    fav_kb = kb(*fav_rows)

    delivered = False
    # Deliver from the channel the file was INDEXED in (falls back to the live
    # channel for legacy docs), so repointing the file channel never serves the
    # wrong file or breaks old results.
    src_channel = f.get("chan_id") or await get_file_channel()
    try:
        if src_channel and f.get("msg_id"):
            await call.bot.copy_message(
                chat_id=uid, from_chat_id=src_channel, message_id=f["msg_id"],
                caption=caption, reply_markup=fav_kb,
            )
            delivered = True
        elif f.get("file_id"):
            await call.bot.send_document(uid, f["file_id"], caption=caption,
                                         reply_markup=fav_kb)
            delivered = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Delivery failed for %s: %s", fuid, exc)

    if not delivered:
        if currency != "VIP":
            await refund(uid, cost, currency)
        await call.message.answer(
            "⚠️ <b>That delivery didn't go through</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No tokens lost — you've been fully refunded.</i>\n\n"
            "<blockquote>"
            "We couldn't hand over this file just now. The copy may have been "
            "removed from the archive, or we've briefly lost access to the source "
            "channel.\n\n"
            "Please try another result, or run a fresh search — we'll find you "
            "another copy."
            "</blockquote>")
        return

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
    # Fire-and-forget so a possible AI classify never slows delivery.
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
    await log_book_found(call.bot, uid, f.get("name") or "", f.get("ext") or "", currency)


# NOTE: req_manual and req_history are handled by requests_manual.py and track.py.
# Do NOT add stub handlers for them here — request.router is included before those,
# so a stub would shadow the real, working features.
