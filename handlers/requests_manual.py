"""
handlers/requests_manual.py — "Request via Admin" (manual fulfilment).

User flow:
  Request Center → 👤 Request Admin → Ebook / Audiobook
    Ebook:     title → author → format (PDF/EPUB/MOBI) → cover → confirm
    Audiobook: title → author → cover → confirm
  Gated by tier (no token cost):
    • FREE  — 1 ebook request / 24h; audiobook requests are premium-only (closed).
    • PREMIUM — 3 ebook + 3 audiobook requests / 24h.
  On confirm: the daily quota is consumed (quota.mreq / mreq_audio), request
  stored, admins notified. Nothing is charged.

Admin flow (admin panel → 📬 Requests, or /requests):
  Cards for each pending request → Send File / Mark Completed / Cancel(+reason).
  • Send File: admin uploads a doc → bot saves a permanent copy to the database
    (file) channel, indexes it into `files` under the requested title (with the
    channel coords for robust delivery) + delivered to the user with an ⭐ Add-to-
    Favorites button, and pings any watchlist waiters. The title is then searchable,
    so the next user who requests it gets it instantly.
  • Cancel: admin types a reason → archived → user notified with the reason
    (no charge, so no refund).

Request doc (`requests`):
  request_id, user_id, first_name, title, author, format, category,
  cover_id, type="manual", status, created_at, cancel_reason?, file_unique_id?
"""
import logging
import random
import string
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from database.connection import MongoManager
from utils.brand import CREDIT
from utils.channel import get_file_channel
from utils.logs import log_request_created, log_request_fulfilled
from utils.files import clean_title, index_file, kind_for_ext, trigrams
from utils.format import fmt_dt
from utils.keyboards import btn, cancel_row, kb
from utils.permissions import has
from utils import premium, quota

logger = logging.getLogger(__name__)
router = Router()


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


# ── tier-gate cards ──────────────────────────────────────────────────────────
async def _audio_locked_card(call: CallbackQuery) -> None:
    """Audiobook concierge requests are premium-only — show the upsell."""
    await call.message.edit_text(
        "🔒 <b>Audiobook requests are a Premium perk</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Hand-sourced audiobooks are reserved for our Premium readers.</i>\n"
        "<blockquote>Free members can request <b>eBooks</b> from a curator any day. "
        "Audiobook concierge requests — <b>3 a day</b>, sourced by hand — open up the "
        "moment you go Premium.</blockquote>\n"
        "<i>💡 Tap below to unlock audiobooks (plus more eBook requests and a lot more).</i>",
        reply_markup=kb([btn("👑 Go Premium", "go_premium", style="success")],
                        [btn("📘 Request an eBook instead", "mreq_ebook", style="primary")],
                        [btn("🔙 Back to Requests", "menu_request", style="danger")]))


async def _limit_card(call: CallbackQuery, key: str, kind: str, label: str) -> None:
    """Daily quota for this request kind is exhausted — show usage + upsell."""
    used, limit = await quota.status(call.from_user.id, key, kind=kind)
    is_prem = await premium.is_premium(call.from_user.id)
    rows = []
    if not is_prem:
        rows.append([btn("👑 Go Premium", "go_premium", style="success")])
    rows.append([btn("🔙 Back to Requests", "menu_request", style="danger")])
    extra = ("" if is_prem else
             "\n<i>💡 Premium readers get more requests every day — and audiobook "
             "concierge requests too.</i>")
    await call.message.edit_text(
        "🛑 <b>Daily request limit reached</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>You've used today's {label} concierge requests.</i>\n"
        "<blockquote>"
        f"📦 <b>{label} requests today:</b> "
        f"<code>{used}/{quota.fmt_limit(limit)}</code>\n"
        "🕛 Your allowance refreshes at midnight (UTC).</blockquote>"
        f"{extra}",
        reply_markup=kb(*rows))


# ── entry ────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "req_manual")
async def cb_req_manual(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_data({})
    await call.message.edit_text(
        "👤 <b>Concierge Request</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Can't find a title in the archive? Hand it to our team — we'll source it for you.</i>\n"
        "<blockquote>Tell us what you're after and we'll track it down, then deliver it "
        "straight to your chat. eBook or audiobook — your choice.\n"
        "🎁 <b>Free of charge</b> — no tokens spent. Just a few requests a day, "
        "so we can give every reader our full attention.</blockquote>\n"
        "<i>👇 What shall we find for you?</i>",
        reply_markup=kb(
            [btn("📘 eBook", "mreq_ebook", style="primary"),
             btn("🎧 Audiobook", "mreq_audio", style="success")],
            [btn("🔙 Back to Requests", "menu_request", style="danger")]))


@router.callback_query(F.data.in_({"mreq_ebook", "mreq_audio"}))
async def cb_pick_category(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    uid = call.from_user.id
    category = "ebook" if call.data == "mreq_ebook" else "audiobook"
    qkey = "mreq" if category == "ebook" else "mreq_audio"

    if category == "audiobook" and not await premium.is_premium(uid):
        await _audio_locked_card(call)
        return
    if not await quota.can(uid, qkey, kind=qkey):
        await _limit_card(call, qkey, qkey,
                          "eBook" if category == "ebook" else "Audiobook")
        return

    await state.update_data(category=category)
    await state.set_state(ManualFSM.title)
    label = "📘 eBook" if category == "ebook" else "🎧 Audiobook"
    await call.message.edit_text(
        f"{label} <b>Request · Step 1 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>✍️ Send us the <b>title</b> of the book you'd like — exactly as "
        "it appears on the cover works best, so we match the right edition.</blockquote>\n"
        "<i>💡 Nothing is charged until you confirm. Tap Cancel anytime to back out.</i>",
        reply_markup=kb([btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.message(ManualFSM.title, F.text)
async def on_title(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        return await _maybe_cancel(message, state)
    await state.update_data(title=message.text.strip())
    await state.set_state(ManualFSM.author)
    await message.answer(
        "✍️ <b>Request · Step 2 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Now the <b>author's name</b>. This helps us pick the right "
        "book when several share a title.</blockquote>",
        reply_markup=kb([btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.message(ManualFSM.author, F.text)
async def on_author(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        return await _maybe_cancel(message, state)
    await state.update_data(author=message.text.strip())
    data = await state.get_data()
    if data.get("category") == "ebook":
        await message.answer(
            "📂 <b>Request · Step 3 of 4</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Pick your preferred <b>format</b> and we'll source that "
            "edition where we can.\n"
            "📑 <b>PDF</b> — pixel-perfect, ideal for textbooks &amp; comics.\n"
            "📘 <b>EPUB</b> — reflows beautifully, best for novels.\n"
            "📙 <b>MOBI</b> — for Kindle libraries.</blockquote>",
            reply_markup=kb([btn("📑 PDF", "mfmt_PDF", style="primary"),
                             btn("📘 EPUB", "mfmt_EPUB", style="primary"),
                             btn("📙 MOBI", "mfmt_MOBI", style="primary")],
                            [btn("❌ Cancel", "mreq_cancel", style="danger")]))
    else:
        await state.set_state(ManualFSM.cover)
        await message.answer(
            "🖼 <b>Request · Step 3 of 4</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Send a <b>cover image</b> (a photo or an image file) so we "
            "can confirm we've matched the exact title.\n"
            "💡 A quick grab from Google Images is perfect.</blockquote>",
            reply_markup=kb([btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.callback_query(F.data.startswith("mfmt_"))
async def cb_format(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.update_data(format=call.data.split("_", 1)[1])
    await state.set_state(ManualFSM.cover)
    await call.message.edit_text(
        "🖼 <b>Request · Step 4 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Almost there — send a <b>cover image</b> (a photo or an image "
        "file) so we lock onto the exact edition.\n"
        "💡 A quick grab from Google Images works perfectly.</blockquote>",
        reply_markup=kb([btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.message(ManualFSM.cover, F.photo | F.document)
async def on_cover(message: Message, state: FSMContext) -> None:
    cover_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await state.update_data(cover_id=cover_id)
    data = await state.get_data()
    fmt = f"📂 <b>Format:</b> {data.get('format')}\n" if data.get("category") == "ebook" else ""
    await message.answer_photo(
        cover_id,
        caption=("📋 <b>Review &amp; Confirm</b>\n"
                 "━━━━━━━━━━━━━━━━━━━━\n"
                 "<i>One last look before we send this to the order desk.</i>\n"
                 "<blockquote>"
                 f"📖 <b>Title:</b> {data.get('title')}\n"
                 f"✍️ <b>Author:</b> {data.get('author')}\n"
                 f"📦 <b>Type:</b> {data.get('category').title()}\n"
                 f"{fmt}"
                 "🎁 <b>Cost:</b> Free — counts toward today's request allowance.</blockquote>\n"
                 "<i>💡 Tap Confirm and we'll take it from here.</i>"),
        reply_markup=kb([btn("✅ Confirm & Submit", "mreq_confirm", style="success")],
                        [btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.message(ManualFSM.cover)
async def on_cover_invalid(message: Message) -> None:
    await message.answer(
        "⚠️ <b>That wasn't quite an image</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the cover as a <b>photo</b> or an <b>image file</b> and "
        "we'll lock onto the right edition. A screenshot from Google Images is "
        "perfect.</blockquote>",
        reply_markup=kb([btn("❌ Cancel", "mreq_cancel", style="danger")]))


@router.callback_query(F.data == "mreq_cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Request cancelled — nothing was charged.")
    await call.message.answer(
        "🛑 <b>Request cancelled</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>No worries — not a single token was touched. Your wallet is "
        "exactly as it was, and the order desk never saw this one.</blockquote>\n"
        "<i>💡 Ready when you are — start a fresh request anytime.</i>",
        reply_markup=kb([btn("🔙 Back to Menu", "menu_home", style="danger")]))


@router.callback_query(F.data == "mreq_confirm")
async def cb_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("title"):
        await call.answer("This request session has expired — please start a fresh one.", show_alert=True)
        await state.clear()
        return
    uid = call.from_user.id
    category = data["category"]
    qkey = "mreq" if category == "ebook" else "mreq_audio"

    # Audiobook concierge requests are premium-only (re-check in case the user's
    # tier changed mid-flow).
    if category == "audiobook" and not await premium.is_premium(uid):
        await call.answer()
        await _audio_locked_card(call)
        await state.clear()
        return

    # Atomically consume one of today's allowance; abort if the limit's been hit.
    if not await quota.consume(uid, qkey, kind=qkey):
        await call.answer()
        await _limit_card(call, qkey, qkey,
                          "eBook" if category == "ebook" else "Audiobook")
        await state.clear()
        return

    await call.answer("Confirmed — your request is on its way to our team.")
    rid = _rid()
    req = {
        "request_id": rid, "user_id": uid,
        "first_name": call.from_user.first_name or "User",
        "title": data["title"], "author": data.get("author", ""),
        "format": data.get("format", ""), "category": category,
        "cover_id": data.get("cover_id"), "type": "manual",
        "status": "pending",
        "created_at": _now(),
    }
    db = await MongoManager.get()
    try:
        await db.safe_insert("requests", req)
    except Exception:  # noqa: BLE001 — give back the consumed allowance if storage fails
        await quota.refund_one(uid, qkey)
        await state.clear()
        await call.message.answer(
            "⚠️ <b>That didn't go through</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>We hit a snag saving your request, so nothing was filed and "
            "your daily allowance wasn't touched.</blockquote>\n"
            "<i>💡 Please try again in a moment.</i>")
        return
    # per-user request counters (for /balance display)
    field = "ebook_requests" if category == "ebook" else "audiobook_requests"
    await db.safe_update("users", {"user_id": uid}, {"$inc": {field: 1}})
    # track reading taste by genre (AI classifies the title) → 🎯 For You shelf
    import asyncio
    from utils.foryou import record_genre_read
    asyncio.create_task(record_genre_read(uid, data["title"]))
    await state.clear()

    await call.message.answer(
        "✨ <b>Request received — we're on it</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your order is in the queue and our team will take it from here.</i>\n"
        "<blockquote>"
        f"🆔 <b>Tracking ID:</b> <code>{rid}</code>\n"
        f"📖 <b>{req['title']}</b> — {req['author']}\n"
        f"🕒 <b>Requested:</b> {fmt_dt(req['created_at'])}</blockquote>\n"
        "<i>🔔 We'll ping you the moment it's ready. Follow its progress anytime "
        "via 🚨 Track Request.</i>")

    # notify admins
    summary = (f"🚀 <b>New Manual {req['category'].title()} Request</b>\n"
               "━━━━━━━━━━━━━━━━━━━━\n"
               "<blockquote>"
               f"🆔 <code>{rid}</code>\n👤 <a href='tg://user?id={uid}'>{req['first_name']}</a> "
               f"(<code>{uid}</code>)\n📖 {req['title']}\n✍️ {req['author']}\n"
               f"📂 {req['format'] or req['category']}\n"
               f"🕒 {fmt_dt(req['created_at'])}</blockquote>")
    for admin in ADMIN_IDS:
        try:
            if req["cover_id"]:
                await call.bot.send_photo(admin, req["cover_id"], caption=summary,
                                          reply_markup=_admin_card_kb(rid))
            else:
                await call.bot.send_message(admin, summary, reply_markup=_admin_card_kb(rid))
        except Exception:  # noqa: BLE001
            pass

    # channels: admin (full detail) + public (curated, privacy-safe)
    await log_request_created(call.bot, uid, req["first_name"], req["title"],
                              req.get("author", ""), req["category"], req.get("cover_id"))


async def _maybe_cancel(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer(
            "🛑 <b>Request cancelled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>All clear — nothing was charged and the order desk never "
            "saw it. Start a fresh request whenever you're ready.</blockquote>")


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
        await bot.send_message(
            admin_id,
            "📭 <b>The queue is clear</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>No manual requests are waiting right now. Every reader is "
            "sorted — beautiful work. 🛡</blockquote>")
        return
    await bot.send_message(
        admin_id,
        "📬 <b>Manual Request Queue</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🛡 <b>{len(pending)}</b> request(s) awaiting fulfilment, oldest "
        "first. Each card below has its own actions — send the file, mark it done, "
        "or cancel with a reason.</blockquote>")
    for r in pending:
        cap = ("🛡 <b>Pending Request</b>\n"
               "━━━━━━━━━━━━━━━━━━━━\n"
               "<blockquote>"
               f"🆔 <code>{r['request_id']}</code>\n👤 <code>{r['user_id']}</code>\n"
               f"📖 {r.get('title')}\n✍️ {r.get('author')}\n"
               f"📂 {r.get('format') or r.get('category')}\n"
               f"🕒 {fmt_dt(r.get('created_at'))}</blockquote>")
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
    if not await has(call.from_user.id, "requests"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    await _render_queue(call.bot, call.from_user.id)


@router.message(Command("requests"))
async def cmd_requests(message: Message) -> None:
    if not await has(message.chat.id, "requests"):
        await message.answer(
            "🔒 You don't have permission for this — ask the owner to enable it.")
        return
    await _render_queue(message.bot, message.chat.id)


async def _get_req(rid: str):
    db = await MongoManager.get()
    return await db.find_one_global("requests", {"request_id": rid})


# ── send file ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("areq_send:"))
async def cb_send_init(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "requests"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await call.answer("This request is no longer pending — it's already been handled.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminReqFSM.awaiting_file)
    await state.update_data(rid=rid, target=req["user_id"])
    await call.message.answer(
        "📤 <b>Deliver the File</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Upload the file for request <code>{rid}</code> now — "
        "a <b>document</b>, <b>audio</b>, or <b>video</b>.\n"
        "🛡 It's saved to the database channel, indexed into the searchable archive "
        "under this title, and delivered straight to the reader — so the next person "
        "who searches it gets it instantly.</blockquote>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(AdminReqFSM.awaiting_file, F.document | F.audio | F.video)
async def on_admin_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rid, target = data.get("rid"), data.get("target")
    await state.clear()
    req = await _get_req(rid)
    if not req:
        await message.answer(
            "❌ <b>Request not found</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This ticket is no longer in the system — it may have been "
            "cancelled or already fulfilled. Nothing was sent.</blockquote>")
        return

    obj = message.document or message.audio or message.video
    file_id = obj.file_id
    fuid = getattr(obj, "file_unique_id", None) or rid
    fname = getattr(obj, "file_name", None) or req.get("title", "file")
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else (req.get("format", "") or "").lower()
    title = clean_title(req.get("title") or fname)
    kind = "audio" if message.audio else ("video" if message.video else kind_for_ext(ext))

    # 1) Save a permanent copy into the bot's database (file) channel so the title
    #    physically lives in the archive — not just a fragile file_id reference.
    #    Stamp the request title as the channel caption so it stays recognisable.
    file_channel = await get_file_channel()
    chan_id = chan_msg_id = None
    if file_channel:
        try:
            sent = await message.bot.copy_message(
                chat_id=file_channel, from_chat_id=message.chat.id,
                message_id=message.message_id, caption=f"📖 {escape(title)}")
            chan_id, chan_msg_id = file_channel, sent.message_id
        except Exception as exc:  # noqa: BLE001 — bot may lack post rights; index by file_id
            logger.warning("Couldn't save fulfilled file to the file channel %s: %s",
                           file_channel, exc)

    # 2) Record it in the searchable archive keyed to the requested title, with the
    #    channel coordinates for robust copy_message delivery (falls back to file_id).
    name_lc = title.lower()
    doc = {"file_unique_id": fuid, "name": title, "name_lc": name_lc,
           "name_tg": trigrams(name_lc), "ext": ext, "kind": kind,
           "chan_id": chan_id, "msg_id": chan_msg_id, "file_id": file_id}
    await index_file(doc)
    # Force the requested title + channel coords to stick even if the live channel-post
    # indexer raced us and inserted a filename-named doc for the same message first.
    db = await MongoManager.get()
    await db.safe_update("files", {"file_unique_id": fuid},
                         {"$set": {"name": title, "name_lc": name_lc,
                                   "name_tg": trigrams(name_lc), "ext": ext, "kind": kind,
                                   "chan_id": chan_id, "msg_id": chan_msg_id,
                                   "file_id": file_id}})

    caption = ("🎁 <b>Your book has arrived</b>\n"
               "━━━━━━━━━━━━━━━━━━━━\n"
               "<i>Sourced and delivered, just as you asked — enjoy.</i>\n"
               "<blockquote>"
               f"📖 <b>{req.get('title')}</b>\n✍️ {req.get('author')}</blockquote>\n"
               "<i>🔖 Tap below to save it to your library so it's always one tap away.</i>\n\n"
               f"{CREDIT}")
    fav = kb([btn("⭐ Save to Favorites", f"fav_add:{fuid}", style="success")])
    try:
        await message.bot.send_document(target, file_id, caption=caption, reply_markup=fav) \
            if message.document else \
            await message.bot.copy_message(target, message.chat.id, message.message_id)
    except Exception as exc:  # noqa: BLE001
        await message.answer(
            "❌ <b>Delivery didn't go through</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>We couldn't hand this file to the reader.\n🛡 <b>Details:</b> "
            f"{exc}</blockquote>\n"
            "<i>💡 The ticket is still open — try sending the file again.</i>")
        return

    # 3) Now that the title is in the archive, notify anyone who searched for it while
    #    it was missing (the same watchlist the live channel indexer services).
    try:
        from handlers.indexer import _service_watchlist
        await _service_watchlist(message.bot, title)
    except Exception:  # noqa: BLE001 — a courtesy ping must never break fulfilment
        pass

    saved_note = ("it's saved to the database channel and indexed in the searchable "
                  "archive" if chan_id else
                  "it's indexed in the searchable archive (no file channel is connected "
                  "yet, so it's stored by file reference — connect one via 🗂 File Channel "
                  "for a permanent copy)")
    await message.answer(
        "✨ <b>File delivered</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>The reader has their book and {saved_note}. From now on, anyone "
        "who searches this title gets it instantly. 🛡 Tap below to close out the "
        "ticket.</blockquote>",
        reply_markup=kb([btn("✅ Mark Completed", f"areq_done:{rid}",
                             style="primary")]))


# ── mark completed ───────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("areq_done:"))
async def cb_done(call: CallbackQuery) -> None:
    if not await has(call.from_user.id, "requests"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await call.answer("This one's already been handled — no action needed.", show_alert=True)
        return
    await db.safe_update("requests", {"request_id": rid},
                         {"$set": {"status": "fulfilled", "fulfilled_at": _now(),
                                   "fulfilled_by": call.from_user.id}})
    # channels: admin (full detail) + public (curated, with the cover photo)
    await log_request_fulfilled(call.bot, req["user_id"], req.get("title") or "",
                                req.get("author") or "", rid, req.get("cover_id"))
    await call.answer("Marked completed — the reader has been notified.")
    try:
        await call.bot.send_message(
            req["user_id"],
            "✅ <b>Request fulfilled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>All done — your book has been delivered.</i>\n"
            "<blockquote>"
            f"🆔 <b>Tracking ID:</b> <code>{rid}</code>\n"
            f"📖 <b>{req.get('title')}</b>\n"
            f"🕒 <b>Fulfilled:</b> {fmt_dt(_now())}</blockquote>\n"
            "<i>🔖 Find it anytime in your library. Happy reading!</i>")
    except Exception:  # noqa: BLE001
        pass


# ── cancel + reason ──────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("areq_cancel:"))
async def cb_cancel_init(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "requests"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await call.answer("This one's already been handled — no action needed.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminReqFSM.awaiting_reason)
    await state.update_data(rid=rid)
    await call.message.answer(
        "📝 <b>Cancel Request</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Type a short <b>reason</b> for cancelling <code>{rid}</code>. "
        "The reader sees this exact note, so keep it kind and clear.</blockquote>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(AdminReqFSM.awaiting_reason, F.text)
async def on_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rid = data.get("rid")
    await state.clear()
    reason = (message.text or "").strip()[:400]
    db = await MongoManager.get()
    req = await _get_req(rid)
    if not req or req.get("status") != "pending":
        await message.answer(
            "❌ <b>Already handled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>This request has already been fulfilled or cancelled, so "
            "nothing further was changed.</blockquote>")
        return

    # Manual requests no longer cost tokens, so there's nothing to refund.
    await db.safe_update("requests", {"request_id": rid},
                         {"$set": {"status": "cancelled", "cancel_reason": reason,
                                   "cancelled_at": _now()}})
    await message.answer(
        "✅ <b>Request cancelled</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🆔 <code>{rid}</code></blockquote>\n"
        "<i>🛡 The reader has been notified with your reason.</i>")
    try:
        await message.bot.send_message(
            req["user_id"],
            "🔔 <b>An update on your request</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>We weren't able to fulfil this one this time.</i>\n"
            "<blockquote>"
            f"🆔 <b>Tracking ID:</b> <code>{rid}</code>\n"
            f"📖 <b>{req.get('title')}</b>\n"
            f"🕒 <b>Cancelled:</b> {fmt_dt(_now())}\n"
            f"📝 <b>Note from our team:</b> {reason}</blockquote>\n"
            "<i>💡 Sorry we missed this one — try another title and we'll do our best to track it down.</i>")
    except Exception:  # noqa: BLE001
        pass
