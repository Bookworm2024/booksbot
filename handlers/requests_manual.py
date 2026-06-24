"""
handlers/requests_manual.py — "Request via Admin" (manual fulfilment).

User flow:
  Request Center → 👤 Request Admin → Ebook / Audiobook
    Ebook:     title → author → format (PDF/EPUB/MOBI) → cover → confirm
    Audiobook: title → author → cover → confirm
  On confirm: 2 tokens deducted (BCN-first), request stored, admins notified.

Admin flow (admin panel → 📬 Requests, or /requests):
  Cards for each pending request → Send File / Mark Completed / Cancel(+reason).
  • Send File: admin uploads a doc → indexed into `files` + delivered to the
    user with an ⭐ Add-to-Favorites button.
  • Cancel: admin types a reason → refund (BGM-only: BCN→25%, BGM→75%) →
    archived → user notified with the reason.

Request doc (`requests`):
  request_id, user_id, first_name, title, author, format, category,
  cover_id, type="manual", currency_used, cost, status, created_at,
  cancel_reason?, file_unique_id?
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

from config import ADMIN_IDS, LOG_CHANNEL_ID
from database.connection import MongoManager
from utils.files import clean_title, index_file, kind_for_ext
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.wallet import get_balances, refund, spend

logger = logging.getLogger(__name__)
router = Router()

_COST = 2.0


class ManualFSM(StatesGroup):
    title = State()
    author = State()
    cover = State()


class AdminReqFSM(StatesGroup):
    awaiting_file = State()
    awaiting_reason = State()


def _rid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def _now():
    return datetime.now(timezone.utc)


# ── entry ────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "req_manual")
async def cb_req_manual(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    from utils.settings import get_float
    cost = await get_float("request_cost")
    bgm, bcn = await get_balances(call.from_user.id)
    if bgm + bcn < cost:
        await call.message.edit_text(
            f"🚫 <b>Insufficient balance.</b>\nManual requests cost <b>{fmt_amount(cost)} tokens</b>.\n"
            f"You have {bgm + bcn:.2f}.",
            reply_markup=kb([btn("💎 Buy BGM", "acc_buy", style="success")],
                            [btn("🔙 Back", "menu_request", style="danger")]))
        return
    await state.set_data({})
    await call.message.edit_text(
        "<b>👤 Admin Request</b>\n\nWhat are you requesting?\n"
        f"💰 Cost: <b>{fmt_amount(cost)} BCN/BGM</b> (deducted on confirm).",
        reply_markup=kb(
            [btn("📘 Ebook", "mreq_ebook", style="primary"),
             btn("🎧 Audiobook", "mreq_audio", style="success")],
            [btn("🔙 Back", "menu_request", style="danger")]))


@router.callback_query(F.data.in_({"mreq_ebook", "mreq_audio"}))
async def cb_pick_category(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    category = "ebook" if call.data == "mreq_ebook" else "audiobook"
    await state.update_data(category=category)
    await state.set_state(ManualFSM.title)
    label = "📘 Ebook" if category == "ebook" else "🎧 Audiobook"
    await call.message.edit_text(
        f"<b>{label} Request — Step 1</b>\n\n✍️ Send the <b>title</b>.\n"
        "<i>Send /cancel anytime to abort.</i>")


@router.message(ManualFSM.title, F.text)
async def on_title(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        return await _maybe_cancel(message, state)
    await state.update_data(title=message.text.strip())
    await state.set_state(ManualFSM.author)
    await message.answer("<b>Step 2</b>\n\n✍️ Send the <b>author's name</b>.")


@router.message(ManualFSM.author, F.text)
async def on_author(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        return await _maybe_cancel(message, state)
    await state.update_data(author=message.text.strip())
    data = await state.get_data()
    if data.get("category") == "ebook":
        await message.answer(
            "<b>Step 3</b>\n\n📂 Choose the <b>format</b>:",
            reply_markup=kb([btn("📑 PDF", "mfmt_PDF", style="primary"),
                             btn("📘 EPUB", "mfmt_EPUB", style="primary"),
                             btn("📙 MOBI", "mfmt_MOBI", style="primary")]))
    else:
        await state.set_state(ManualFSM.cover)
        await message.answer("<b>Step 3</b>\n\n🖼 Send the <b>cover image</b> "
                             "(photo or file). Grab one from Google Images.")


@router.callback_query(F.data.startswith("mfmt_"))
async def cb_format(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.update_data(format=call.data.split("_", 1)[1])
    await state.set_state(ManualFSM.cover)
    await call.message.edit_text("<b>Step 4</b>\n\n🖼 Send the <b>cover image</b> "
                                 "(photo or file).")


@router.message(ManualFSM.cover, F.photo | F.document)
async def on_cover(message: Message, state: FSMContext) -> None:
    cover_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await state.update_data(cover_id=cover_id)
    data = await state.get_data()
    from utils.settings import get_float
    cost = await get_float("request_cost")
    fmt = f"\n📂 <b>Format:</b> {data.get('format')}" if data.get("category") == "ebook" else ""
    await message.answer_photo(
        cover_id,
        caption=("<b>⚡ Confirm Request</b>\n"
                 f"📖 <b>Title:</b> {data.get('title')}\n"
                 f"✍️ <b>Author:</b> {data.get('author')}\n"
                 f"📂 <b>Type:</b> {data.get('category').title()}{fmt}\n"
                 f"💰 <b>Cost:</b> {fmt_amount(cost)} BCN/BGM"),
        reply_markup=kb([btn("✅ Approve & Submit", "mreq_confirm", style="success")],
                        [btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.message(ManualFSM.cover)
async def on_cover_invalid(message: Message) -> None:
    await message.answer("⚠️ Please send a <b>photo</b> or <b>image file</b> as the cover.")


@router.callback_query(F.data == "mreq_cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Cancelled")
    await call.message.answer("🛑 <b>Request cancelled.</b> No tokens deducted.",
                              reply_markup=kb([btn("🔙 Menu", "menu_home", style="danger")]))


@router.callback_query(F.data == "mreq_confirm")
async def cb_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("title"):
        await call.answer("Session expired — start again.", show_alert=True)
        await state.clear()
        return
    uid = call.from_user.id
    from utils.settings import get_float
    cost = await get_float("request_cost")
    currency = await spend(uid, cost)
    if not currency:
        await call.answer("Insufficient balance.", show_alert=True)
        await state.clear()
        return
    await call.answer()
    rid = _rid()
    req = {
        "request_id": rid, "user_id": uid,
        "first_name": call.from_user.first_name or "User",
        "title": data["title"], "author": data.get("author", ""),
        "format": data.get("format", ""), "category": data["category"],
        "cover_id": data.get("cover_id"), "type": "manual",
        "currency_used": currency, "cost": cost, "status": "pending",
        "created_at": _now(),
    }
    db = await MongoManager.get()
    await db.safe_insert("requests", req)
    # per-user request counters (for /balance display)
    field = "ebook_requests" if data["category"] == "ebook" else "audiobook_requests"
    await db.safe_update("users", {"user_id": uid}, {"$inc": {field: 1}})
    await state.clear()

    await call.message.answer(
        "✅ <b>Request Registered!</b>\n\n"
        f"🆔 <b>Tracking ID:</b> <code>{rid}</code>\n"
        f"📖 {req['title']} — {req['author']}\n\n"
        "<i>You'll be notified once it's processed. Track it via 🚨 Track Request.</i>")

    # notify admins
    summary = (f"🚀 <b>New Manual {req['category'].title()} Request</b>\n"
               f"🆔 <code>{rid}</code>\n👤 <a href='tg://user?id={uid}'>{req['first_name']}</a> "
               f"(<code>{uid}</code>)\n📖 {req['title']}\n✍️ {req['author']}\n"
               f"📂 {req['format'] or req['category']}\n💰 {currency}")
    for admin in ADMIN_IDS:
        try:
            if req["cover_id"]:
                await call.bot.send_photo(admin, req["cover_id"], caption=summary,
                                          reply_markup=_admin_card_kb(rid))
            else:
                await call.bot.send_message(admin, summary, reply_markup=_admin_card_kb(rid))
        except Exception:  # noqa: BLE001
            pass


async def _maybe_cancel(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("🛑 Cancelled.")


# ── admin queue ────────────────────────────────────────────────────────────────
def _admin_card_kb(rid: str):
    return kb([btn("📤 Send File", f"areq_send:{rid}", style="success")],
              [btn("✅ Mark Completed", f"areq_done:{rid}", style="primary"),
               btn("❌ Cancel", f"areq_cancel:{rid}", style="danger")])


async def _render_queue(bot, admin_id: int) -> None:
    db = await MongoManager.get()
    pending = await db.find_global("requests", {"status": "pending", "type": "manual"},
                                   sort=[("created_at", 1)], limit=10)
    if not pending:
        await bot.send_message(admin_id, "📭 <b>No pending manual requests.</b>")
        return
    await bot.send_message(admin_id, f"📬 <b>{len(pending)} pending request(s):</b>")
    for r in pending:
        cap = (f"🆔 <code>{r['request_id']}</code>\n👤 <code>{r['user_id']}</code>\n"
               f"📖 {r.get('title')}\n✍️ {r.get('author')}\n"
               f"📂 {r.get('format') or r.get('category')}")
        try:
            if r.get("cover_id"):
                await bot.send_photo(admin_id, r["cover_id"], caption=cap,
                                     reply_markup=_admin_card_kb(r["request_id"]))
            else:
                await bot.send_message(admin_id, cap,
                                       reply_markup=_admin_card_kb(r["request_id"]))
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data == "admin_requests")
async def cb_admin_requests(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await _render_queue(call.bot, call.from_user.id)


@router.message(Command("requests"))
async def cmd_requests(message: Message) -> None:
    if message.chat.id not in ADMIN_IDS:
        await message.answer("🚫 Access denied.")
        return
    await _render_queue(message.bot, message.chat.id)


async def _get_req(rid: str):
    db = await MongoManager.get()
    return await db.find_one_global("requests", {"request_id": rid})


# ── send file ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("areq_send:"))
async def cb_send_init(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await call.answer("Request not pending.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminReqFSM.awaiting_file)
    await state.update_data(rid=rid, target=req["user_id"])
    await call.message.answer(f"📁 Send the file for <code>{rid}</code> now "
                              "(document / audio / video).")


@router.message(AdminReqFSM.awaiting_file, F.document | F.audio | F.video)
async def on_admin_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rid, target = data.get("rid"), data.get("target")
    await state.clear()
    req = await _get_req(rid)
    if not req:
        await message.answer("❌ Request vanished.")
        return

    obj = message.document or message.audio or message.video
    file_id = obj.file_id
    fuid = getattr(obj, "file_unique_id", None) or rid
    fname = getattr(obj, "file_name", None) or req.get("title", "file")
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else (req.get("format", "") or "").lower()

    # enrich the searchable archive with the fulfilled file
    await index_file({
        "file_unique_id": fuid, "name": clean_title(req.get("title", fname)),
        "name_lc": clean_title(req.get("title", fname)).lower(), "ext": ext,
        "kind": "audio" if message.audio else ("video" if message.video else kind_for_ext(ext)),
        "msg_id": None, "file_id": file_id,
    })

    caption = (f"📚 <b>Your requested file is ready!</b>\n\n"
               f"📖 {req.get('title')}\n✍️ {req.get('author')}\n\n"
               "❤️ @bookslibraryofficial")
    fav = kb([btn("⭐ Add to Favorites", f"fav_add:{fuid}", style="success")])
    try:
        await message.bot.send_document(target, file_id, caption=caption, reply_markup=fav) \
            if message.document else \
            await message.bot.copy_message(target, message.chat.id, message.message_id)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"❌ Could not deliver to user: {exc}")
        return
    await message.answer("✅ File delivered. Tap below to close the ticket.",
                         reply_markup=kb([btn("✅ Mark Completed", f"areq_done:{rid}",
                                              style="primary")]))


# ── mark completed ───────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("areq_done:"))
async def cb_done(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await call.answer("Already processed.", show_alert=True)
        return
    await db.safe_update("requests", {"request_id": rid},
                         {"$set": {"status": "fulfilled", "fulfilled_at": _now(),
                                   "fulfilled_by": call.from_user.id}})
    await call.answer("Marked completed ✅")
    try:
        await call.bot.send_message(req["user_id"],
                                    f"✅ <b>Request fulfilled</b>\n🆔 <code>{rid}</code>\n"
                                    f"📖 {req.get('title')}")
    except Exception:  # noqa: BLE001
        pass


# ── cancel + refund ────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("areq_cancel:"))
async def cb_cancel_init(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await call.answer("Already processed.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminReqFSM.awaiting_reason)
    await state.update_data(rid=rid)
    await call.message.answer(f"📝 Type the <b>cancellation reason</b> for <code>{rid}</code> "
                              "(sent to the user):")


@router.message(AdminReqFSM.awaiting_reason, F.text)
async def on_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rid = data.get("rid")
    await state.clear()
    reason = (message.text or "").strip()[:400]
    db = await MongoManager.get()
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await message.answer("❌ Already processed.")
        return

    # refund — always in BGM: BCN→25%, BGM→75% of cost
    rate = 0.25 if req.get("currency_used") == "BCN" else 0.75
    refund_amt = round(req.get("cost", _COST) * rate, 3)
    await refund(req["user_id"], refund_amt, "BGM")
    await db.safe_update("requests", {"request_id": rid},
                         {"$set": {"status": "cancelled", "cancel_reason": reason,
                                   "refunded": refund_amt, "cancelled_at": _now()}})
    await message.answer(f"✅ Cancelled <code>{rid}</code> · refunded {fmt_amount(refund_amt)} BGM.")
    try:
        await message.bot.send_message(
            req["user_id"],
            f"❌ <b>Request Cancelled</b>\n🆔 <code>{rid}</code>\n📖 {req.get('title')}\n\n"
            f"📭 <b>Reason:</b> {reason}\n💰 <b>Refund:</b> {fmt_amount(refund_amt)} BGM")
    except Exception:  # noqa: BLE001
        pass
