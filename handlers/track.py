"""
handlers/track.py — request tracking & history.

  /track  (or 🚨 Track Request) — user enters a request id → status (own only)
  📜 My History — paginated list of the user's requests
  /track_request — admin: look up ANY request id
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from database.connection import MongoManager
from utils.keyboards import btn, kb

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
    await message.answer("🔍 Send the <b>Request ID</b> to track:")


@router.message(TrackFSM.awaiting_id, F.text)
async def on_track_id(message: Message, state: FSMContext) -> None:
    await state.clear()
    rid = (message.text or "").strip().upper()
    db = await MongoManager.get()
    req = await db.find_one_global("requests", {"request_id": rid})
    if not req:
        await message.answer("❌ No request found with that ID.")
        return
    if req.get("user_id") != message.chat.id:
        await message.answer("🚫 That request isn't yours.")
        return
    await message.answer(_render_req(req))


def _render_req(req: dict) -> str:
    status = _STATUS.get(req.get("status"), "❓")
    extra = ""
    if req.get("status") == "cancelled":
        extra = (f"\n📭 <b>Reason:</b> {req.get('cancel_reason', '—')}"
                 f"\n💰 <b>Refunded:</b> {req.get('refunded', 0)} BGM")
    return ("📦 <b>Request Status</b>\n"
            f"🆔 <code>{req.get('request_id')}</code>\n"
            f"📖 {req.get('title')}\n✍️ {req.get('author')}\n"
            f"📂 {req.get('format') or req.get('category')}\n"
            f"📊 <b>Status:</b> {status}{extra}")


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
            "📭 <b>No requests yet.</b>",
            reply_markup=kb([btn("🔙 Back", "menu_request", style="danger")]))
        return
    pages = (len(reqs) + _PER_PAGE - 1) // _PER_PAGE
    page = max(0, min(page, pages - 1))
    chunk = reqs[page * _PER_PAGE:(page + 1) * _PER_PAGE]
    lines = ["📜 <b>Your Request History</b>\n"]
    for r in chunk:
        lines.append(f"{_STATUS.get(r.get('status'),'❓')} <code>{r['request_id']}</code> — "
                     f"{r.get('title','?')[:30]}")
    rows = []
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", f"hist_pg:{page-1}", style="primary"))
    if page + 1 < pages:
        nav.append(btn("Next ➡️", f"hist_pg:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 Back", "menu_request", style="danger")])
    await call.message.edit_text("\n".join(lines) + f"\n\nPage {page+1}/{pages}",
                                 reply_markup=kb(*rows))


# ── admin track ────────────────────────────────────────────────────────────────
@router.message(Command("track_request"))
async def cmd_track_admin(message: Message, state: FSMContext) -> None:
    if message.chat.id not in ADMIN_IDS:
        await message.answer("🚫 Access denied.")
        return
    await state.set_state(TrackFSM.awaiting_admin_id)
    await message.answer("🔍 Send the Request ID to look up:")


@router.message(TrackFSM.awaiting_admin_id, F.text)
async def on_admin_track(message: Message, state: FSMContext) -> None:
    await state.clear()
    rid = (message.text or "").strip().upper()
    db = await MongoManager.get()
    req = await db.find_one_global("requests", {"request_id": rid})
    if not req:
        await message.answer("❌ No request found.")
        return
    await message.answer(_render_req(req) + f"\n👤 <b>User:</b> <code>{req.get('user_id')}</code>")
