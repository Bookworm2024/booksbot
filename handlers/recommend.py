"""
handlers/recommend.py — AI book recommendations.

  /recommend (or 🤖 AI Recommendations) → Proceed (1 token, BCN-first) →
  type a genre → Claude returns ~100 titles, shown 20 at a time
  (Get More / End). Invalid genre → refund (BCN→0.75 BGM, BGM→0.9 BGM).
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.ai import ai_enabled, recommend_titles, summarize_book
from utils.keyboards import btn, kb
from utils.wallet import get_balances, refund, spend

logger = logging.getLogger(__name__)
router = Router()

_COST = 1.0
_BATCH = 20


class RecFSM(StatesGroup):
    awaiting_genre = State()
    awaiting_summary_title = State()


@router.message(Command("recommend"))
async def cmd_recommend(message: Message) -> None:
    await _intro(message, message.chat.id)


@router.callback_query(F.data == "lib_recommend")
async def cb_recommend(call: CallbackQuery) -> None:
    await call.answer()
    await _intro(call.message, call.from_user.id)


async def _intro(message: Message, uid: int) -> None:
    if not await ai_enabled():
        await message.answer("🤖 AI recommendations are turned off right now "
                             "(admin: enable AI in /admin).")
        return
    bgm, bcn = await get_balances(uid)
    if bgm + bcn < _COST:
        await message.answer("❌ <b>Insufficient balance.</b> AI recs cost 1 BCN/BGM.")
        return
    await message.answer(
        "✨ <b>AI Book Recommendations</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Get 100 hand-picked titles in any genre.\n\n"
        f"💎 Cost: <b>1 BCN/BGM</b>\n💳 Balance: {bcn:.2f} BCN · {bgm:.2f} BGM",
        reply_markup=kb([btn("🚀 Proceed & Pay", "rec_proceed", style="success")],
                        [btn("🔙 Back", "menu_library", style="danger")]))


@router.callback_query(F.data == "rec_proceed")
async def cb_proceed(call: CallbackQuery, state: FSMContext) -> None:
    uid = call.from_user.id
    currency = await spend(uid, _COST)
    if not currency:
        await call.answer("Insufficient balance.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RecFSM.awaiting_genre)
    await state.update_data(currency=currency)
    await call.message.edit_text(
        "🎯 <b>Enter a genre</b>\n\nType any genre — e.g. <i>fantasy, cyberpunk, "
        "self-help, dark academia</i>. /cancel to abort.")


@router.message(RecFSM.awaiting_genre, F.text)
async def on_genre(message: Message, state: FSMContext) -> None:
    genre = (message.text or "").strip()
    if genre.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    data = await state.get_data()
    currency = data.get("currency", "BGM")
    await state.clear()
    uid = message.chat.id

    notice = await message.answer(f"🔍 Searching for <b>{genre}</b> titles…")
    titles = await recommend_titles(genre)

    if not titles:
        refund_amt = 0.75 if currency == "BCN" else 0.9
        await refund(uid, refund_amt, "BGM")
        await notice.edit_text(
            f"❌ <b>{genre}</b> isn't a genre I could use.\n"
            f"💸 Refunded <b>{refund_amt} BGM</b>. Try another genre via 🤖 AI Recommendations.")
        return

    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"rec_genre": genre, "rec_titles": titles, "rec_sent": 0}})
    await notice.delete()
    await _send_batch(message, uid)


async def _send_batch(message: Message, uid: int) -> None:
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"rec_titles": 1, "rec_sent": 1, "rec_genre": 1}) or {}
    titles = u.get("rec_titles") or []
    sent = int(u.get("rec_sent") or 0)
    genre = u.get("rec_genre") or "your"
    batch = titles[sent:sent + _BATCH]
    if not batch:
        await message.answer("📕 <b>End of list</b> — that's all 100! Start a new genre anytime.",
                             reply_markup=kb([btn("🔄 New Genre", "lib_recommend", style="success")]))
        return
    lines = "\n".join(f"<code>{sent + i + 1:>3}.</code> {t}" for i, t in enumerate(batch))
    await db.safe_update("users", {"user_id": uid}, {"$set": {"rec_sent": sent + len(batch)}})
    more = (sent + len(batch)) < len(titles)
    rows = []
    if more:
        rows.append([btn("🔄 Get More", "rec_more", style="success")])
    rows.append([btn("🛑 End Session", "rec_end", style="danger")])
    await message.answer(
        f"📚 <b>{genre.title()} — picks {sent + 1}–{sent + len(batch)}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n" + lines, reply_markup=kb(*rows))


@router.callback_query(F.data == "rec_more")
async def cb_more(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await _send_batch(call.message, call.from_user.id)


@router.callback_query(F.data == "rec_end")
async def cb_end(call: CallbackQuery) -> None:
    await call.answer("Session ended")
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": call.from_user.id},
                         {"$set": {"rec_titles": [], "rec_sent": 0, "rec_genre": ""}})
    await call.message.edit_text("✅ <b>Session ended.</b> Happy reading!",
                                 reply_markup=kb([btn("🏠 Menu", "menu_home", style="primary")]))


# ── AI book summary ─────────────────────────────────────────────────────────
@router.message(Command("summary"))
async def cmd_summary(message: Message) -> None:
    await _summary_intro(message, message.chat.id)


@router.callback_query(F.data == "lib_summary")
async def cb_summary(call: CallbackQuery) -> None:
    await call.answer()
    await _summary_intro(call.message, call.from_user.id)


async def _summary_intro(message: Message, uid: int) -> None:
    if not await ai_enabled():
        await message.answer("📝 AI summaries are turned off right now (admin: enable AI in /admin).")
        return
    bgm, bcn = await get_balances(uid)
    if bgm + bcn < _COST:
        await message.answer("❌ <b>Insufficient balance.</b> A summary costs 1 BCN/BGM.")
        return
    await message.answer(
        "📝 <b>AI Book Summary</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Get a crisp, spoiler-light summary of any book — overview, themes, "
        "who it's for, and key takeaways.\n\n"
        f"💎 Cost: <b>1 BCN/BGM</b> · Balance: {bcn:.2f} BCN · {bgm:.2f} BGM",
        reply_markup=kb([btn("🚀 Proceed & Pay", "sum_proceed", style="success")],
                        [btn("🔙 Back", "menu_library", style="danger")]))


@router.callback_query(F.data == "sum_proceed")
async def cb_sum_proceed(call: CallbackQuery, state: FSMContext) -> None:
    uid = call.from_user.id
    currency = await spend(uid, _COST)
    if not currency:
        await call.answer("Insufficient balance.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RecFSM.awaiting_summary_title)
    await state.update_data(currency=currency)
    await call.message.edit_text("✍️ <b>Send the book title</b> (and author if you can). "
                                 "/cancel to abort.")


@router.message(RecFSM.awaiting_summary_title, F.text)
async def on_summary_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if title.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    data = await state.get_data()
    currency = data.get("currency", "BGM")
    await state.clear()
    uid = message.chat.id
    notice = await message.answer(f"📝 Summarizing <b>{title}</b>…")
    summary = await summarize_book(title)
    if not summary:
        refund_amt = 0.75 if currency == "BCN" else 0.9
        await refund(uid, refund_amt, "BGM")
        await notice.edit_text(
            f"❌ I couldn't find <b>{title}</b>. Refunded <b>{refund_amt} BGM</b>. "
            "Try the exact title + author.")
        return
    await notice.edit_text(
        f"📘 <b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n{summary}",
        reply_markup=kb([btn("📝 Another Summary", "lib_summary", style="success")],
                        [btn("🔙 Library", "menu_library", style="danger")]))
