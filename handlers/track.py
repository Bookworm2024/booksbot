"""
handlers/track.py — request tracking & history.

  /track  (or 🚨 Track Request) — user enters a request id → status (own only)
  📜 My History — paginated list of the user's requests
  /track_request — admin: look up ANY request id
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.format import fmt_amount, fmt_dt
from utils.keyboards import btn, kb
from utils.permissions import has

logger = logging.getLogger(__name__)
router = Router()

_STATUS = {"pending": "⏳ Pending", "fulfilled": "✅ Fulfilled", "cancelled": "❌ Cancelled"}
_PER_PAGE = 5


class TrackFSM(StatesGroup):
    awaiting_id = State()
    awaiting_admin_id = State()


# ── user track ─────────────────────────────────────────────────────────────────
@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext) -> None:
    await _prompt(message, state, TrackFSM.awaiting_id)


@router.callback_query(F.data == "acc_track")
async def cb_track(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _prompt(call.message, state, TrackFSM.awaiting_id)


async def _prompt(message: Message, state: FSMContext, st) -> None:
    await state.set_state(st)
    await message.answer(
        "🔭 <b>Track Your Request</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Live status for every title you've asked us to find.</i>\n"
        "<blockquote>Send the <b>Request ID</b> from your confirmation message and "
        "we'll pull up exactly where it stands — pending in the queue, fulfilled and "
        "ready, or cancelled with a refund.</blockquote>\n"
        "<i>💡 Your ID looks like <code>R-XXXXXX</code> — paste it just as it appears.</i>")


@router.message(TrackFSM.awaiting_id, F.text)
async def on_track_id(message: Message, state: FSMContext) -> None:
    await state.clear()
    rid = (message.text or "").strip().upper()
    db = await MongoManager.get()
    req = await db.find_one_global("requests", {"request_id": rid})
    if not req:
        await message.answer(
            "❌ <b>No Match for That ID</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>We couldn't find a request under that ID. Double-check the "
            "characters and try again — it's case-insensitive, so spacing or a stray "
            "letter is usually the culprit.</blockquote>\n"
            "<i>💡 Lost the ID? Open <b>📜 My History</b> to see all your requests in one place.</i>")
        return
    if req.get("user_id") != message.chat.id:
        await message.answer(
            "🔒 <b>That Request Isn't on Your Account</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>For your privacy, you can only track requests tied to your own "
            "account. If this should be yours, make sure you're using the same Telegram "
            "account you placed it from.</blockquote>")
        return
    await message.answer(_render_req(req))


def _render_req(req: dict) -> str:
    status = _STATUS.get(req.get("status"), "❓")
    # full UTC timeline so progress is easy to track at a glance
    times = [f"🕒 <b>Requested:</b> {fmt_dt(req.get('created_at'))}"]
    if req.get("status") == "fulfilled":
        times.append(f"✅ <b>Fulfilled:</b> {fmt_dt(req.get('fulfilled_at'))}")
    elif req.get("status") == "cancelled":
        times.append(f"❌ <b>Cancelled:</b> {fmt_dt(req.get('cancelled_at'))}")
    timeline = "\n".join(times)
    extra = ""
    if req.get("status") == "cancelled":
        # Freemium requests are free, so cancellations carry no refund; only legacy
        # cancellations that actually refunded BGM show the refund line.
        refunded = float(req.get("refunded") or 0)
        refund_line = (f"\n💎 <b>Refunded:</b> <code>{fmt_amount(refunded)}</code> BGM "
                       "— credited back to your wallet, no action needed."
                       if refunded > 0 else "")
        extra = (f"\n\n<blockquote>📭 <b>Reason:</b> {escape(str(req.get('cancel_reason') or '—'))}"
                 f"{refund_line}</blockquote>")
    return ("📦 <b>Request Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Order ID:</b> <code>{escape(str(req.get('request_id') or ''))}</code>\n"
            f"<blockquote>📖 <b>Title:</b> {escape(str(req.get('title') or ''))}\n"
            f"✍️ <b>Author:</b> {escape(str(req.get('author') or ''))}\n"
            f"📂 <b>Format:</b> {escape(str(req.get('format') or req.get('category') or ''))}\n"
            f"📊 <b>Status:</b> {status}\n"
            f"{timeline}</blockquote>"
            f"{extra}")


# ── history ────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "req_history")
async def cb_history(call: CallbackQuery) -> None:
    await call.answer()
    await _render_history(call, 0)


@router.callback_query(F.data.startswith("hist_pg:"))
async def cb_history_pg(call: CallbackQuery) -> None:
    await call.answer()
    await _render_history(call, int(call.data.split(":", 1)[1]))


async def _render_history(call: CallbackQuery, page: int) -> None:
    db = await MongoManager.get()
    reqs = await db.find_global("requests", {"user_id": call.from_user.id},
                                sort=[("created_at", -1)])
    if not reqs:
        await call.message.edit_text(
            "📜 <b>Your Request History</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>No requests just yet — this is where every title you ask us to "
            "track down will appear, with live status and refunds on anything we can't "
            "find. Search a book or place a request to start the list.</blockquote>\n"
            "<i>💡 Tip: keep your <b>Order ID</b> handy to check status anytime.</i>",
            reply_markup=kb([btn("🔙 Back to Requests", "menu_request", style="danger")]))
        return
    pages = (len(reqs) + _PER_PAGE - 1) // _PER_PAGE
    page = max(0, min(page, pages - 1))
    chunk = reqs[page * _PER_PAGE:(page + 1) * _PER_PAGE]
    lines = ["📜 <b>Your Request History</b>",
             "━━━━━━━━━━━━━━━━━━━━",
             "<i>Every title you've asked us to find — newest first.</i>",
             "<blockquote>"]
    for r in chunk:
        lines.append(f"{_STATUS.get(r.get('status'),'❓')} <code>{escape(str(r.get('request_id') or ''))}</code> — "
                     f"{escape(str(r.get('title') or '?')[:28])}")
        lines.append(f"   🕒 {fmt_dt(r.get('created_at'))}")
    lines.append("</blockquote>")
    rows = []
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Newer", f"hist_pg:{page-1}", style="primary"))
    if page + 1 < pages:
        nav.append(btn("Older ➡️", f"hist_pg:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 Back to Requests", "menu_request", style="danger")])
    await call.message.edit_text(
        "\n".join(lines)
        + f"\n\n<i>📄 Page <code>{page+1}</code> of <code>{pages}</code> — "
          "tap an Order ID's status anytime via 🔭 Track Request.</i>",
        reply_markup=kb(*rows))


# ── admin track ────────────────────────────────────────────────────────────────
@router.message(Command("track_request"))
async def cmd_track_admin(message: Message, state: FSMContext) -> None:
    if not await has(message.chat.id, "requests"):
        await message.answer(
            "🔒 <b>Permission Needed</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>You don't have permission for this — ask the owner to enable it. "
            "To check your own orders, use <b>🔭 Track Request</b> or <b>📜 My History</b> "
            "instead.</blockquote>")
        return
    await state.set_state(TrackFSM.awaiting_admin_id)
    await message.answer(
        "🛡 <b>Admin · Request Lookup</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send any <b>Request ID</b> to pull its full status, format and the "
        "owning user — across every account, not just your own.</blockquote>")


@router.message(TrackFSM.awaiting_admin_id, F.text)
async def on_admin_track(message: Message, state: FSMContext) -> None:
    await state.clear()
    rid = (message.text or "").strip().upper()
    db = await MongoManager.get()
    req = await db.find_one_global("requests", {"request_id": rid})
    if not req:
        await message.answer(
            "❌ <b>No Match for That ID</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Nothing on record under that Request ID. Confirm the exact "
            "characters and try the lookup again.</blockquote>")
        return
    await message.answer(
        _render_req(req)
        + f"\n\n<blockquote>👤 <b>Owner:</b> <code>{req.get('user_id')}</code></blockquote>")
