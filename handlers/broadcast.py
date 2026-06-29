"""
handlers/broadcast.py — admin broadcast engine.

Admin → 📡 Broadcast (or /broadcast) → send the message → pick an AUDIENCE
(all / VIP / active / inactive / legacy) → send NOW or SCHEDULE (in N hours) →
it copies that message to the segment, rate-limited, with a live progress card
(Pause / Resume / Stop / Refresh). State lives in the `broadcasts` collection so
progress survives a refresh; the worker re-reads status each batch so pause/stop
take effect mid-run. Scheduled broadcasts fire from run_scheduled_broadcasts().
"""
import asyncio
import logging
import random
import string
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, cancel_row, kb
from utils.permissions import has

logger = logging.getLogger(__name__)
router = Router()

_BATCH = 25            # reload control flags every N sends
_SLEEP = 0.05          # ~20 msgs/sec — safely under Telegram limits

# audience segments → human label; filter is built at run time (see _audience_filter)
SEG_LABELS = {
    "all":      "👥 Everyone",
    "vip":      "👑 VIP members",
    "active":   "🟢 Active this week",
    "inactive": "😴 Win-back (quiet 7d+)",
    "legacy":   "📦 Imported readers",
}


def _audience_filter(seg: str) -> dict:
    now = _now()
    if seg == "vip":
        return {"vip_until": {"$gt": now}}
    if seg == "active":
        return {"last_active": {"$gte": now - timedelta(days=7)}}
    if seg == "inactive":
        return {"$or": [{"last_active": {"$lt": now - timedelta(days=7)}},
                        {"last_active": {"$exists": False}}]}
    if seg == "legacy":
        return {"imported": True}
    return {}   # all


async def _count_seg(db, seg: str) -> int:
    return await db.count_global("users", _audience_filter(seg))


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
        row.append(btn("⏸ Pause delivery", f"bc_act:pause:{bid}", style="primary"))
    elif status == "paused":
        row.append(btn("▶️ Resume delivery", f"bc_act:resume:{bid}", style="success"))
    if status in ("running", "paused"):
        row.append(btn("🛑 Stop campaign", f"bc_act:stop:{bid}", style="danger"))
    return kb(row, [btn("🔄 Refresh progress", f"bc_refresh:{bid}", style="primary")])


async def _progress_text(bid: str) -> tuple[str, str]:
    db = await MongoManager.get()
    b = await db.find_one_global("broadcasts", {"bid": bid}) or {}
    status = b.get("status", "done")
    total, sent, failed = b.get("total", 0), b.get("sent", 0), b.get("failed", 0)
    icon = {"running": "⚡", "paused": "⏸", "stopped": "🛑", "done": "✅"}.get(status, "•")
    phrase = {"running": "Delivering now", "paused": "Paused — ready to resume",
              "stopped": "Stopped by you", "done": "Delivered"}.get(status, "Standing by")
    text = (f"📡 <b>Campaign {bid}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icon} <b>{status.upper()}</b> · <i>{phrase}</i>\n"
            f"{_bar(sent, total)}\n"
            f"<blockquote>"
            f"👥 Audience — <code>{total}</code> readers\n"
            f"✅ Delivered — <code>{sent}</code>\n"
            f"❌ Couldn't reach — <code>{failed}</code>"
            f"</blockquote>\n"
            f"<i>💡 Tap Refresh for the latest count — delivery runs in the background.</i>")
    return text, status


# ── entry ────────────────────────────────────────────────────────────────────
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    from utils.permissions import has
    if not await has(message.chat.id, "broadcast"):
        await message.answer(
            "🛡 <b>Broadcast Studio</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            "This tool sends announcements to your readers, so it's reserved for "
            "team members with the <b>broadcast</b> permission.\n\n"
            "If you need access, ask a super admin to grant it from Admin → Permissions."
            "</blockquote>")
        return
    await _open(message, state)


@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    from utils.permissions import has
    if not await has(call.from_user.id, "broadcast"):
        await call.answer("Broadcasting is reserved for admins with the broadcast permission. Ask a super admin for access.", show_alert=True)
        return
    await call.answer()
    await _open(call.message, state)


async def _open(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastFSM.awaiting_content)
    await message.answer(
        "📡 <b>Broadcast Studio</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Reach your readers with a single, polished message.</i>\n"
        "<blockquote>"
        "📝 <b>Step 1 — Compose</b>\n"
        "Send the exact message you'd like to broadcast — text, a photo, a "
        "document, anything Telegram supports. Formatting, buttons and media all "
        "carry through exactly as you send them.\n\n"
        "🎯 <b>Next</b>\n"
        "You'll choose <b>who</b> receives it, then send <b>now</b> or schedule it "
        "for later — with a live delivery board you can pause or stop at any time."
        "</blockquote>\n"
        "<i>💡 Tap Cancel below anytime to back out — nothing goes out until you confirm.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(BroadcastFSM.awaiting_content)
async def on_content(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.answer(
            "✅ <b>Broadcast cancelled</b>\n"
            "<i>Nothing was sent — your readers heard nothing. Start again whenever you're ready.</i>")
        return
    # keep the source message coords; leave the content-capture state
    await state.set_state(None)
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    db = await MongoManager.get()
    rows = []
    for seg, label in SEG_LABELS.items():
        n = await _count_seg(db, seg)
        rows.append([btn(f"{label} · {n}", f"bc_aud:{seg}", style="primary")])
    rows.append([btn("❌ Cancel broadcast", "menu_home", style="danger")])
    await message.answer(
        "📡 <b>Step 2 — Choose your audience</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Send to everyone, or target exactly the readers who matter.</i>\n"
        "<blockquote>"
        "👥 <b>Everyone</b> — every reader on the bot\n"
        "👑 <b>VIP members</b> — your premium subscribers\n"
        "🟢 <b>Active this week</b> — opened the bot in the last 7 days\n"
        "😴 <b>Win-back</b> — quiet for 7+ days, ready to re-engage\n"
        "📦 <b>Imported readers</b> — your legacy, migrated audience"
        "</blockquote>\n"
        "<i>💡 The number beside each segment is its live size right now. Pick one to continue.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("bc_aud:"))
async def cb_audience(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "broadcast"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    seg = call.data.split(":", 1)[1]
    if seg not in SEG_LABELS:
        await call.answer("That audience is no longer available — please pick another.", show_alert=True)
        return
    await state.update_data(seg=seg)
    await call.answer()
    db = await MongoManager.get()
    n = await _count_seg(db, seg)
    await call.message.edit_text(
        f"⏰ <b>Step 3 — When should it go out?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Send instantly, or queue it for the perfect moment.</i>\n"
        f"<blockquote>"
        f"🎯 Audience — <b>{SEG_LABELS[seg]}</b>\n"
        f"👥 Reaching — <code>{n}</code> readers\n\n"
        f"🚀 <b>Send now</b> — delivery begins immediately\n"
        f"⏰ <b>Schedule</b> — fires in 1, 6 or 24 hours, even if you step away"
        f"</blockquote>\n"
        f"<i>💡 Scheduled? Keep the message you composed in this chat until it fires.</i>",
        reply_markup=kb(
            [btn("🚀 Send now", "bc_when:0", style="success")],
            [btn("⏰ In 1 hour", "bc_when:1", style="primary"),
             btn("⏰ In 6 hours", "bc_when:6", style="primary"),
             btn("⏰ In 24 hours", "bc_when:24", style="primary")],
            [btn("❌ Cancel broadcast", "menu_home", style="danger")]))


@router.callback_query(F.data.startswith("bc_when:"))
async def cb_when(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "broadcast"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    try:
        hours = int(call.data.split(":", 1)[1])
    except ValueError:
        hours = 0
    data = await state.get_data()
    src_chat, src_msg = data.get("src_chat"), data.get("src_msg")
    seg = data.get("seg", "all")
    if not src_chat or not src_msg:
        await call.answer("This session timed out. Send your message again to start a fresh broadcast.", show_alert=True)
        return
    await state.clear()
    db = await MongoManager.get()
    total = await _count_seg(db, seg)
    bid = _bid()
    base = {"bid": bid, "segment": seg, "total": total, "sent": 0, "failed": 0,
            "src_chat": int(src_chat), "src_msg": int(src_msg),
            "started_by": call.from_user.id, "created_at": _now()}

    if hours <= 0:
        base["status"] = "running"
        await db.safe_insert("broadcasts", base)
        asyncio.create_task(_run(call.bot, bid))
        await call.answer("Launching your broadcast — we'll take it from here.")
        text, status = await _progress_text(bid)
        await call.message.edit_text(text, reply_markup=_progress_kb(bid, status))
    else:
        send_at = _now() + timedelta(hours=hours)
        base.update({"status": "scheduled", "send_at": send_at})
        await db.safe_insert("broadcasts", base)
        await call.answer("Scheduled — we'll send it on time, even if you're away.")
        await call.message.edit_text(
            f"⏰ <b>Campaign scheduled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Queued and ready — it'll go out on its own.</i>\n"
            f"<blockquote>"
            f"🆔 Campaign — <code>{bid}</code>\n"
            f"🎯 Audience — <b>{SEG_LABELS[seg]}</b> · <code>{total}</code> readers\n"
            f"🕒 Fires — <b>{send_at.strftime('%d %b %H:%M UTC')}</b> · in {hours}h"
            f"</blockquote>\n"
            f"<i>💡 Keep the message you composed in this chat until then — that's the copy we'll deliver.</i>",
            reply_markup=kb([btn("🔙 Back to Admin", "admin_open", style="primary")]))


async def _run(bot, bid: str) -> None:
    db = await MongoManager.get()
    b = await db.find_one_global("broadcasts", {"bid": bid})
    if not b:
        return
    src_chat, src_msg = b["src_chat"], b["src_msg"]
    seg = b.get("segment", "all")
    users = await db.find_global("users", _audience_filter(seg), proj={"user_id": 1})
    # refresh total to the live audience size (esp. for scheduled sends)
    await db.safe_update("broadcasts", {"bid": bid},
                         {"$set": {"total": len(users)}}, upsert=False)
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
    if not await has(call.from_user.id, "broadcast"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    _, action, bid = call.data.split(":")
    new = {"pause": "paused", "resume": "running", "stop": "stopped"}.get(action)
    db = await MongoManager.get()
    await db.safe_update("broadcasts", {"bid": bid}, {"$set": {"status": new}}, upsert=False)
    toast = {"pause": "Paused — delivery is on hold. Resume whenever you're ready.",
             "resume": "Resumed — delivery is rolling again.",
             "stop": "Stopped — no further messages will go out for this campaign."
             }.get(action, f"{action.title()}d")
    await call.answer(toast)
    text, status = await _progress_text(bid)
    await call.message.edit_text(text, reply_markup=_progress_kb(bid, status))


@router.callback_query(F.data.startswith("bc_refresh:"))
async def cb_refresh(call: CallbackQuery) -> None:
    if not await has(call.from_user.id, "broadcast"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    bid = call.data.split(":", 1)[1]
    text, status = await _progress_text(bid)
    await call.message.edit_text(text, reply_markup=_progress_kb(bid, status))


# ── scheduled-broadcast worker (started in bot.py) ──────────────────────────────
async def run_scheduled_broadcasts(bot) -> None:
    """Fire due scheduled broadcasts. Polls every 30s; claims each atomically
    (scheduled → running) so it can't be sent twice."""
    while True:
        try:
            db = await MongoManager.get()
            due = await db.find_global(
                "broadcasts", {"status": "scheduled", "send_at": {"$lte": _now()}},
                proj={"bid": 1})
            for d in due:
                claimed = await db.find_one_and_update_global(
                    "broadcasts", {"bid": d["bid"], "status": "scheduled"},
                    {"$set": {"status": "running", "started_at": _now()}})
                if claimed:
                    logger.info("Firing scheduled broadcast %s", d["bid"])
                    asyncio.create_task(_run(bot, d["bid"]))
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            logger.warning("scheduled-broadcast loop error: %s", exc)
        await asyncio.sleep(30)
