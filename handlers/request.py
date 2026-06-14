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
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import FILE_CHANNEL_ID, LOG_CHANNEL_ID
from database.connection import MongoManager
from utils.files import get_file, icon_for, search
from utils.keyboards import btn, kb
from utils.wallet import get_balances, refund, spend

logger = logging.getLogger(__name__)
router = Router()

_PER_PAGE = 8
_DOWNLOAD_COST = 1.0
_NORM_RE = re.compile(r"[^a-z0-9 ]+")


class RequestFSM(StatesGroup):
    awaiting_query = State()


def _norm(text: str) -> str:
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


# ── Request Center ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu_request")
async def cb_request_center(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        "<b>📚 Request Center</b>\n\n"
        "🤖 <b>Request Bot</b> — instant search of our 24/7 archive.\n"
        "👤 <b>Request Admin</b> — for rare titles not in the archive.",
        reply_markup=kb(
            [btn("🤖 Request Bot", "req_auto", style="success"),
             btn("👤 Request Admin", "req_manual", style="primary")],
            [btn("📜 My History", "req_history", style="primary")],
            [btn("🔙 Back", "menu_home", style="danger")],
        ),
    )


@router.callback_query(F.data == "req_auto")
async def cb_req_auto(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(RequestFSM.awaiting_query)
    await call.message.edit_text(
        "🔍 <b>Search the Archive</b>\n\n"
        "Send the <b>title</b> or keywords of the book/audiobook you want.\n\n"
        "📝 Example: <code>atomic habits</code>",
        reply_markup=kb([btn("🔙 Back", "menu_request", style="danger")]),
    )


@router.message(RequestFSM.awaiting_query, F.text)
async def on_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if query.startswith("/"):
        await state.clear()
        return
    await state.update_data(sq=query, sp=0)
    await _render_results(message, state, query, 0, edit=False)


@router.callback_query(F.data.in_({"sr_next", "sr_prev"}))
async def cb_page(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    query = data.get("sq", "")
    page = int(data.get("sp", 0)) + (1 if call.data == "sr_next" else -1)
    page = max(0, page)
    await state.update_data(sp=page)
    await _render_results(call.message, state, query, page, edit=True)


async def _render_results(message: Message, state: FSMContext, query: str,
                          page: int, *, edit: bool) -> None:
    results, total = await search(query, skip=page * _PER_PAGE, limit=_PER_PAGE)

    if total == 0:
        await _add_watchlist(message.chat.id, query)
        text = ("❌ <b>No matches found.</b>\n\n"
                f"✨ Added <code>{query}</code> to your <b>Watchlist</b> — I'll DM you "
                "the moment it's uploaded.\n\nOr request it from an admin for priority:")
        markup = kb([btn("👤 Request from Admin", "req_manual", style="primary")],
                    [btn("🔙 Back", "menu_request", style="danger")])
        await (message.edit_text if edit else message.answer)(text, reply_markup=markup)
        return

    rows = []
    for f in results:
        label = f"{icon_for(f.get('ext',''))} {f.get('name','Untitled')[:40]}"
        rows.append([btn(label, f"dl:{f['file_unique_id']}", style="success")])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", "sr_prev", style="primary"))
    if (page + 1) * _PER_PAGE < total:
        nav.append(btn("Next ➡️", "sr_next", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔍 New Search", "req_auto", style="primary"),
                 btn("🔙 Menu", "menu_home", style="danger")])

    pages = (total + _PER_PAGE - 1) // _PER_PAGE
    text = (f"🔍 <b>Results for</b> <code>{query}</code>\n"
            f"📊 {total} match(es) · page {page + 1}/{pages}\n\n"
            f"💸 Cost: <b>1 BCN/BGM</b> per download.")
    await (message.edit_text if edit else message.answer)(text, reply_markup=kb(*rows))


async def _add_watchlist(user_id: int, query: str) -> None:
    db = await MongoManager.get()
    await db.safe_update(
        "watchlist", {"user_id": user_id, "query_norm": _norm(query)},
        {"$set": {"user_id": user_id, "query": query, "query_norm": _norm(query),
                  "matched": False, "created_at": datetime.now(timezone.utc)}},
    )


# ── Download / delivery ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("dl:"))
async def cb_download(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("This file is no longer available.", show_alert=True)
        return

    bgm, bcn = await get_balances(uid)
    if bgm + bcn < _DOWNLOAD_COST:
        await call.answer()
        await call.message.answer(
            "❌ <b>Insufficient balance.</b>\nYou need 1 BCN/BGM to download.\n"
            "💡 Use /claim for free BCN or buy BGM.",
            reply_markup=kb([btn("💎 Buy BGM", "acc_buy", style="success")]),
        )
        return

    currency = await spend(uid, _DOWNLOAD_COST)
    if not currency:
        await call.answer("Balance changed — not enough tokens.", show_alert=True)
        return

    await call.answer("📤 Sending…")
    caption = (f"{icon_for(f.get('ext',''))} <b>{f.get('name','Your File')}</b>\n\n"
               "❤️ Presented by @bookslibraryofficial")
    fav_kb = kb([btn("⭐ Add to Favorites", f"fav_add:{fuid}", style="success")])

    delivered = False
    try:
        if FILE_CHANNEL_ID and f.get("msg_id"):
            await call.bot.copy_message(
                chat_id=uid, from_chat_id=FILE_CHANNEL_ID, message_id=f["msg_id"],
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
        await refund(uid, _DOWNLOAD_COST, currency)
        await call.message.answer(
            "❌ <b>Delivery failed.</b> Your token was refunded.\n"
            "<i>The file may have been removed, or I'm not in the archive channel.</i>")
        return

    # success bookkeeping
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$inc": {"downloads": 1}})
    from utils.files import bump_download
    await bump_download(fuid)
    if LOG_CHANNEL_ID:
        try:
            await call.bot.send_message(
                LOG_CHANNEL_ID,
                f"📦 <b>File Sent</b>\n👤 <code>{uid}</code>\n"
                f"📚 {f.get('name')}\n💰 {currency}")
        except Exception:  # noqa: BLE001
            pass


# ── stubs (later phases) ─────────────────────────────────────────────────────────
@router.callback_query(F.data.in_({"req_manual", "req_history"}))
async def cb_req_stub(call: CallbackQuery) -> None:
    await call.answer()
    msg = ("👤 Admin requests arrive in the requests phase."
           if call.data == "req_manual" else "📜 Request history arrives soon.")
    await call.message.edit_text(msg, reply_markup=kb([btn("🔙 Back", "menu_request",
                                                           style="danger")]))
