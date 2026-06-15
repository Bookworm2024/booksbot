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
        "🚩 <b>Report a problem</b>\n\nDescribe the issue — a bad or broken file, "
        "wrong content, or abuse. /cancel to abort.")


@router.message(ReportFSM.text, F.text)
async def on_report(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    await state.clear()
    db = await MongoManager.get()
    rid = _rid()
    await db.safe_insert("reports", {
        "rid": rid, "user_id": message.chat.id, "text": raw[:1000],
        "status": "open", "created_at": datetime.now(timezone.utc),
    })
    await message.answer(
        f"✅ <b>Report received</b> (<code>{rid}</code>). Thanks — our team will review it.")
    if LOG_CHANNEL_ID:
        try:
            await message.bot.send_message(
                LOG_CHANNEL_ID,
                f"🚩 <b>New Report {rid}</b>\n👤 <code>{message.chat.id}</code>\n{raw[:500]}")
        except Exception:  # noqa: BLE001
            pass
