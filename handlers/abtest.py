"""
handlers/abtest.py — A/B broadcast.

Admin → 🧰 More Tools → 🧪 A/B Test: send two message variants, pick an audience,
and the engine splits it ~50/50 (variant A to even-indexed recipients, B to odd)
and reports per-variant delivery. Reuses broadcast's audience segments but has its
own simple fire-and-track engine (no pause/resume) so it never touches the proven
broadcast worker.

Gated on the 'broadcast' permission (utils.permissions).
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

from database.connection import MongoManager
from handlers.broadcast import SEG_LABELS, _audience_filter
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_SLEEP = 0.05


def _now():
    return datetime.now(timezone.utc)


def _aid() -> str:
    return "AB" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


class ABFSM(StatesGroup):
    variant_a = State()
    variant_b = State()


async def _gate(uid: int) -> bool:
    from utils.permissions import has
    return await has(uid, "broadcast")


@router.callback_query(F.data == "admin_abtest")
async def cb_abtest(call: CallbackQuery, state: FSMContext) -> None:
    if not await _gate(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(ABFSM.variant_a)
    await call.message.answer(
        "🧪 <b>A/B Test</b>\n\nSend <b>Variant A</b> (the first message). /cancel to abort.")


@router.message(Command("abtest"))
async def cmd_abtest(message: Message, state: FSMContext) -> None:
    if not await _gate(message.chat.id):
        await message.answer("🚫 Access denied.")
        return
    await state.set_state(ABFSM.variant_a)
    await message.answer("🧪 <b>A/B Test</b>\n\nSend <b>Variant A</b>. /cancel to abort.")


@router.message(ABFSM.variant_a)
async def on_variant_a(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.update_data(a_chat=message.chat.id, a_msg=message.message_id)
    await state.set_state(ABFSM.variant_b)
    await message.answer("✅ Got Variant A. Now send <b>Variant B</b>. /cancel to abort.")


@router.message(ABFSM.variant_b)
async def on_variant_b(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.update_data(b_chat=message.chat.id, b_msg=message.message_id)
    await state.set_state(None)
    db = await MongoManager.get()
    rows = []
    for seg, label in SEG_LABELS.items():
        n = await db.count_global("users", _audience_filter(seg))
        rows.append([btn(f"{label} — {n}", f"ab_aud:{seg}", style="primary")])
    rows.append([btn("❌ Cancel", "menu_home", style="danger")])
    await message.answer("🧪 <b>Choose audience</b> for the A/B test:", reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("ab_aud:"))
async def cb_aud(call: CallbackQuery, state: FSMContext) -> None:
    if not await _gate(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    seg = call.data.split(":", 1)[1]
    if seg not in SEG_LABELS:
        await call.answer("Unknown audience", show_alert=True); return
    data = await state.get_data()
    a = (data.get("a_chat"), data.get("a_msg"))
    b = (data.get("b_chat"), data.get("b_msg"))
    if not all(a) or not all(b):
        await call.answer("Session expired — restart the A/B test.", show_alert=True); return
    await state.clear()
    aid = _aid()
    db = await MongoManager.get()
    await db.safe_insert("abtests", {
        "aid": aid, "segment": seg, "a_chat": a[0], "a_msg": a[1],
        "b_chat": b[0], "b_msg": b[1], "sent_a": 0, "sent_b": 0, "failed": 0,
        "status": "running", "started_by": call.from_user.id, "created_at": _now()})
    asyncio.create_task(_run_ab(call.bot, aid))
    await call.answer("Launching…")
    await call.message.edit_text(await _card(aid),
                                 reply_markup=kb([btn("🔄 Refresh", f"ab_refresh:{aid}", style="primary")]))


async def _card(aid: str) -> str:
    db = await MongoManager.get()
    t = await db.find_one_global("abtests", {"aid": aid}) or {}
    sa, sb, fa = int(t.get("sent_a") or 0), int(t.get("sent_b") or 0), int(t.get("failed") or 0)
    status = t.get("status", "done")
    icon = {"running": "⚡", "done": "✅"}.get(status, "•")
    return (f"🧪 <b>A/B Test {aid}</b>\n{icon} <b>{status.upper()}</b>\n"
            f"👥 Audience: <b>{SEG_LABELS.get(t.get('segment','all'),'all')}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🅰️ Variant A delivered: <b>{sa}</b>\n"
            f"🅱️ Variant B delivered: <b>{sb}</b>\n"
            f"❌ Failed: <b>{fa}</b>\n\n"
            "<i>Compare downstream activity between the two groups to pick a winner.</i>")


async def _run_ab(bot, aid: str) -> None:
    db = await MongoManager.get()
    t = await db.find_one_global("abtests", {"aid": aid})
    if not t:
        return
    users = await db.find_global("users", _audience_filter(t.get("segment", "all")),
                                 proj={"user_id": 1})
    sa = sb = failed = 0
    for i, u in enumerate(users):
        is_a = (i % 2 == 0)
        src_chat, src_msg = (t["a_chat"], t["a_msg"]) if is_a else (t["b_chat"], t["b_msg"])
        try:
            await bot.copy_message(u["user_id"], src_chat, src_msg)
            if is_a:
                sa += 1
            else:
                sb += 1
        except Exception:  # noqa: BLE001 — blocked/deactivated recipients
            failed += 1
        if i % 25 == 0:
            await db.safe_update("abtests", {"aid": aid},
                                 {"$set": {"sent_a": sa, "sent_b": sb, "failed": failed}},
                                 upsert=False)
        await asyncio.sleep(_SLEEP)
    await db.safe_update("abtests", {"aid": aid},
                         {"$set": {"sent_a": sa, "sent_b": sb, "failed": failed,
                                   "status": "done", "finished_at": _now()}}, upsert=False)


@router.callback_query(F.data.startswith("ab_refresh:"))
async def cb_refresh(call: CallbackQuery) -> None:
    await call.answer()
    aid = call.data.split(":", 1)[1]
    await call.message.edit_text(
        await _card(aid),
        reply_markup=kb([btn("🔄 Refresh", f"ab_refresh:{aid}", style="primary")]))
