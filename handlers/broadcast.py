"""
handlers/broadcast.py — admin broadcast engine.

Admin → 📡 Broadcast (or /broadcast) → send the message to broadcast → confirm
→ it copies that message to every user, rate-limited, with a live progress card
(Pause / Resume / Stop / Refresh). State lives in the `broadcasts` collection so
progress survives a refresh; the worker re-reads status each batch so pause/stop
take effect mid-run.
"""
import asyncio
import logging
import random
import string
from datetime import datetime, timezone

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

_BATCH = 25            # reload control flags every N sends
_SLEEP = 0.05          # ~20 msgs/sec — safely under Telegram limits


class BroadcastFSM(StatesGroup):
    awaiting_content = State()


def _bid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _now():
    return datetime.now(timezone.utc)


def _bar(sent: int, total: int) -> str:
    pct = int((sent / total) * 10) if total else 0
    return "🟩" * pct + "⬜" * (10 - pct)


def _progress_kb(bid: str, status: str):
    row = []
    if status == "running":
        row.append(btn("⏸ Pause", f"bc_act:pause:{bid}", style="primary"))
    elif status == "paused":
        row.append(btn("▶️ Resume", f"bc_act:resume:{bid}", style="success"))
    if status in ("running", "paused"):
        row.append(btn("🛑 Stop", f"bc_act:stop:{bid}", style="danger"))
    return kb(row, [btn("🔄 Refresh", f"bc_refresh:{bid}", style="primary")])


async def _progress_text(bid: str) -> tuple[str, str]:
    db = await MongoManager.get()
    b = await db.find_one_global("broadcasts", {"bid": bid}) or {}
    status = b.get("status", "done")
    total, sent, failed = b.get("total", 0), b.get("sent", 0), b.get("failed", 0)
    icon = {"running": "⚡", "paused": "⏸", "stopped": "🛑", "done": "✅"}.get(status, "•")
    text = (f"📡 <b>Broadcast {bid}</b>\n"
            f"{icon} <b>{status.upper()}</b>\n{_bar(sent, total)}\n\n"
            f"👥 Total: <code>{total}</code>\n✅ Sent: <code>{sent}</code>\n"
            f"❌ Failed: <code>{failed}</code>")
    return text, status


# ── entry ────────────────────────────────────────────────────────────────────
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if message.chat.id not in ADMIN_IDS:
        await message.answer("🚫 Access denied.")
        return
    await _open(message, state)


@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await _open(call.message, state)


async def _open(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastFSM.awaiting_content)
    await message.answer("📡 <b>Broadcast</b>\n\nSend the message (text/photo/etc.) to "
                         "broadcast to all users. /cancel to abort.")


@router.message(BroadcastFSM.awaiting_content)
async def on_content(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    await state.clear()
    db = await MongoManager.get()
    total = await db.count_global("users")
    await state.update_data()
    # stash the source message coords for copy_message
    await message.answer(
        f"📡 <b>Confirm Broadcast</b>\nAudience: <b>{total}</b> users.\n\n"
        "The message above this prompt will be sent. Proceed?",
        reply_markup=kb([btn("🚀 Start", f"bc_start:{message.chat.id}:{message.message_id}",
                             style="success")],
                        [btn("❌ Cancel", "menu_home", style="danger")]))


@router.callback_query(F.data.startswith("bc_start:"))
async def cb_start(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    _, chat_id, msg_id = call.data.split(":")
    await call.answer("Launching…")
    db = await MongoManager.get()
    total = await db.count_global("users")
    bid = _bid()
    await db.safe_insert("broadcasts", {
        "bid": bid, "status": "running", "total": total, "sent": 0, "failed": 0,
        "src_chat": int(chat_id), "src_msg": int(msg_id),
        "started_by": call.from_user.id, "created_at": _now(),
    })
    asyncio.create_task(_run(call.bot, bid))
    text, status = await _progress_text(bid)
    await call.message.edit_text(text, reply_markup=_progress_kb(bid, status))


async def _run(bot, bid: str) -> None:
    db = await MongoManager.get()
    b = await db.find_one_global("broadcasts", {"bid": bid})
    if not b:
        return
    src_chat, src_msg = b["src_chat"], b["src_msg"]
    users = await db.find_global("users", {}, proj={"user_id": 1})
    sent = failed = 0
    for i, u in enumerate(users):
        if i % _BATCH == 0:
            cur = await db.find_one_global("broadcasts", {"bid": bid}, {"status": 1})
            status = (cur or {}).get("status")
            if status == "stopped":
                break
            while status == "paused":
                await asyncio.sleep(2)
                cur = await db.find_one_global("broadcasts", {"bid": bid}, {"status": 1})
                status = (cur or {}).get("status")
                if status == "stopped":
                    break
            if status == "stopped":
                break
            await db.safe_update("broadcasts", {"bid": bid},
                                 {"$set": {"sent": sent, "failed": failed}}, upsert=False)
        try:
            await bot.copy_message(u["user_id"], src_chat, src_msg)
            sent += 1
        except Exception:  # noqa: BLE001 — blocked/deactivated users
            failed += 1
        await asyncio.sleep(_SLEEP)

    final = "stopped" if (await db.find_one_global("broadcasts", {"bid": bid}, {"status": 1}) or {}
                          ).get("status") == "stopped" else "done"
    await db.safe_update("broadcasts", {"bid": bid},
                         {"$set": {"status": final, "sent": sent, "failed": failed,
                                   "finished_at": _now()}}, upsert=False)


@router.callback_query(F.data.startswith("bc_act:"))
async def cb_action(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    _, action, bid = call.data.split(":")
    new = {"pause": "paused", "resume": "running", "stop": "stopped"}.get(action)
    db = await MongoManager.get()
    await db.safe_update("broadcasts", {"bid": bid}, {"$set": {"status": new}}, upsert=False)
    await call.answer(f"{action.title()}d")
    text, status = await _progress_text(bid)
    await call.message.edit_text(text, reply_markup=_progress_kb(bid, status))


@router.callback_query(F.data.startswith("bc_refresh:"))
async def cb_refresh(call: CallbackQuery) -> None:
    await call.answer()
    bid = call.data.split(":", 1)[1]
    text, status = await _progress_text(bid)
    await call.message.edit_text(text, reply_markup=_progress_kb(bid, status))
