"""
handlers/report.py — users report bad/broken content or abuse (→ Mongo `reports`).

/report or the 🚩 Report button → describe the issue → stored for admins
(viewable under 🧰 More Tools → 🚩 Reports). A copy is pinged to the log channel.
"""
import logging
import random
import string
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import LOG_CHANNEL_ID
from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


class ReportFSM(StatesGroup):
    text = State()


def _rid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    await _start(message, state)


@router.callback_query(F.data == "menu_report")
async def cb_report(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, state)


async def _start(message: Message, state: FSMContext) -> None:
    await state.set_state(ReportFSM.text)
    await message.answer(
        "🛡 <b>Report a problem</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>You're doing the right thing — we'll take it from here.</i>\n\n"
        "<blockquote>Tell us what's wrong in your own words — a broken or corrupt "
        "file, the wrong content, a mislabelled title, or anything abusive.\n\n"
        "Your report is <b>private</b> and goes straight to our team. The more detail "
        "you share, the faster we can put it right.</blockquote>\n\n"
        "<i>Send your message below, or tap <code>/cancel</code> to step away.</i>")


@router.message(ReportFSM.text, F.text)
async def on_report(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ <b>Report cancelled</b>\n"
            "<i>Nothing was sent. If something still isn't right, /report is here "
            "whenever you need it.</i>")
        return
    await state.clear()
    db = await MongoManager.get()
    rid = _rid()
    await db.safe_insert("reports", {
        "rid": rid, "user_id": message.chat.id, "text": raw[:1000],
        "status": "open", "created_at": datetime.now(timezone.utc),
    })
    await message.answer(
        "✅ <b>Report received</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<i>Thank you for flagging this — we've got it from here.</i>\n\n"
        f"<blockquote>Your reference: <code>{rid}</code>\n\n"
        "Our team reviews every report personally and acts on what we find. Keep "
        "this code in case you'd like to follow up.</blockquote>")
    if LOG_CHANNEL_ID:
        try:
            await message.bot.send_message(
                LOG_CHANNEL_ID,
                f"🚩 <b>New Report {rid}</b>\n👤 <code>{message.chat.id}</code>\n{raw[:500]}")
        except Exception:  # noqa: BLE001
            pass
