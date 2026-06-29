"""
handlers/recommend.py — AI book recommendations.

  /recommend (or 🤖 AI Recommendations) → Proceed (1 token, BCN-first) →
  type a genre → Claude returns ~100 titles, shown 20 at a time
  (Get More / End). Invalid genre → refund (BCN→0.75 BGM, BGM→0.9 BGM).
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.ai import (ai_enabled, mood_titles, recommend_titles, similar_titles,
                      summarize_book)
from utils.format import fmt_amount
from utils.keyboards import btn, cancel_row, kb
from utils.settings import get_float
from utils.wallet import get_balances, refund, spend

logger = logging.getLogger(__name__)
router = Router()

_BATCH = 20


async def _ai_cost() -> float:
    return await get_float("ai_cost")


class RecFSM(StatesGroup):
    awaiting_genre = State()
    awaiting_summary_title = State()
    awaiting_similar_title = State()
    awaiting_mood = State()


@router.message(Command("recommend"))
async def cmd_recommend(message: Message) -> None:
    await _intro(message, message.chat.id)


@router.callback_query(F.data == "lib_recommend")
async def cb_recommend(call: CallbackQuery) -> None:
    await call.answer()
    await _intro(call.message, call.from_user.id)


async def _intro(message: Message, uid: int) -> None:
    from utils.flags import is_on
    if not await is_on("recommend") or not await ai_enabled():
        await message.answer(
            "🤖 <b>Your Librarian Is Off Duty</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>AI recommendations are paused at the moment, so personalised "
            "picks aren't available just now. Everything else in your library remains "
            "open — browse Discover or search any title while we bring your librarian "
            "back online.</blockquote>\n"
            "<i>💡 Admins can re-enable AI from /admin.</i>")
        return
    bgm, bcn = await get_balances(uid)
    cost = await _ai_cost()
    if bgm + bcn < cost:
        await message.answer(
            "🔒 <b>Not Quite Enough To Begin</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>A personal recommendation list costs "
            f"<code>{fmt_amount(cost)}</code> BCN or BGM, and your wallet is a little "
            "short right now.</blockquote>\n"
            "<i>💡 Claim your free daily 🪙 BCN or top up 💎 BGM, then your librarian "
            "will be ready when you are.</i>")
        return
    await message.answer(
        "🤖 <b>AI Recommendations</b>\n"
        "<i>Your personal librarian, ready to curate.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Tell us what you're in the mood for and we'll hand-pick reads "
        "tailored to you — three ways to begin:\n\n"
        "🎯 <b>By Genre</b> — up to 100 standout titles in any genre you name\n"
        "📚 <b>Similar To A Book</b> — more of what you loved, matched on theme and vibe\n"
        "🎭 <b>By Mood</b> — describe a feeling (cozy, fast, dark…) and we'll find the "
        "fit</blockquote>\n"
        f"💎 <b>Cost:</b> <code>{fmt_amount(cost)}</code> BCN / BGM per list\n"
        f"💼 <b>Your balance:</b> <code>{bcn:.2f}</code> BCN · <code>{bgm:.2f}</code> BGM\n"
        "<i>💡 Spend dries up your free 🪙 BCN first, so your 💎 BGM stays untouched.</i>",
        reply_markup=kb([btn("🎯 By Genre", "rec_proceed", style="success")],
                        [btn("📚 Similar to a Book", "rec_similar", style="success")],
                        [btn("🎭 By Mood", "rec_mood", style="success")],
                        [btn("🔙 Back", "menu_library", style="danger")]))


@router.callback_query(F.data == "rec_proceed")
async def cb_proceed(call: CallbackQuery, state: FSMContext) -> None:
    uid = call.from_user.id
    currency = await spend(uid, await _ai_cost())
    if not currency:
        await call.answer("Your wallet's a touch short — claim your daily BCN or top up BGM to begin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RecFSM.awaiting_genre)
    await state.update_data(currency=currency)
    await call.message.edit_text(
        "🎯 <b>Name Your Genre</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Type a genre and your librarian will curate up to 100 titles "
        "worth your time.\n\n"
        "Try <i>fantasy</i>, <i>cyberpunk</i>, <i>self-help</i> or <i>dark academia</i> — "
        "the more specific you are, the sharper the picks.</blockquote>\n"
        "<i>💡 Changed your mind? Tap Cancel below.</i>",
        reply_markup=kb(cancel_row("menu_library")))


@router.message(RecFSM.awaiting_genre, F.text)
async def on_genre(message: Message, state: FSMContext) -> None:
    genre = (message.text or "").strip()
    if genre.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "↩️ <b>No Problem</b>\n"
            "<i>Your recommendation request has been set aside — nothing was charged. "
            "Return any time you'd like a fresh list.</i>")
        return
    data = await state.get_data()
    currency = data.get("currency", "BGM")
    await state.clear()
    uid = message.chat.id

    notice = await message.answer(
        f"🔭 <b>Curating your {escape(genre)} shelf…</b>\n"
        "<i>Your librarian is pulling together the best of the genre — one moment.</i>")
    titles = await recommend_titles(genre)

    if not titles:
        refund_amt = round(await _ai_cost() * (0.75 if currency == "BCN" else 0.9), 3)
        await refund(uid, refund_amt, "BGM")
        await notice.edit_text(
            f"🤔 <b>Couldn't Place “{escape(genre)}”</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>That didn't read as a genre your librarian could shelve, so "
            "nothing was curated this time. We've put the spend straight back into your "
            f"wallet — <b><code>{fmt_amount(refund_amt)}</code> BGM</b> returned.</blockquote>\n"
            "<i>💡 Try something a little clearer — <b>historical fiction</b>, "
            "<b>space opera</b>, <b>true crime</b> — via 🤖 AI Recommendations.</i>")
        return

    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"rec_genre": genre, "rec_titles": titles, "rec_sent": 0}})
    await notice.delete()
    await _send_batch(message, uid)


@router.callback_query(F.data == "rec_similar")
async def cb_similar(call: CallbackQuery, state: FSMContext) -> None:
    uid = call.from_user.id
    currency = await spend(uid, await _ai_cost())
    if not currency:
        await call.answer("Your wallet's a touch short — claim your daily BCN or top up BGM to begin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RecFSM.awaiting_similar_title)
    await state.update_data(currency=currency)
    await call.message.edit_text(
        "📚 <b>More Like A Book You Loved</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Name a title that stayed with you and your librarian will track "
        "down reads with the same genre, themes and feel.\n\n"
        "Add the author if you can — it helps us match the right book.</blockquote>\n"
        "<i>💡 Changed your mind? Tap Cancel below.</i>",
        reply_markup=kb(cancel_row("menu_library")))


@router.callback_query(F.data == "rec_mood")
async def cb_mood(call: CallbackQuery, state: FSMContext) -> None:
    uid = call.from_user.id
    currency = await spend(uid, await _ai_cost())
    if not currency:
        await call.answer("Your wallet's a touch short — claim your daily BCN or top up BGM to begin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RecFSM.awaiting_mood)
    await state.update_data(currency=currency)
    await call.message.edit_text(
        "🎭 <b>Tell Us The Mood</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Describe the feeling you're after and your librarian will match "
        "books to the vibe — no titles needed.\n\n"
        "Try <i>cozy rainy-day</i>, <i>fast-paced thriller</i> or "
        "<i>dark academia</i>.</blockquote>\n"
        "<i>💡 Changed your mind? Tap Cancel below.</i>",
        reply_markup=kb(cancel_row("menu_library")))


async def _deliver_or_refund(message: Message, uid: int, currency: str, label: str,
                             titles: list | None, notice) -> None:
    if not titles:
        refund_amt = round(await _ai_cost() * (0.75 if currency == "BCN" else 0.9), 3)
        await refund(uid, refund_amt, "BGM")
        await notice.edit_text(
            f"🤔 <b>Nothing To Shelve For “{escape(label)}”</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your librarian couldn't build a confident list from that, so "
            "we didn't curate one — and we've returned the spend in full: "
            f"<b><code>{fmt_amount(refund_amt)}</code> BGM</b> back in your "
            "wallet.</blockquote>\n"
            "<i>💡 Try again with a clearer book title or a more vivid mood — "
            "specifics give us the best picks.</i>")
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"rec_genre": label, "rec_titles": titles, "rec_sent": 0}})
    await notice.delete()
    await _send_batch(message, uid)


@router.message(RecFSM.awaiting_similar_title, F.text)
async def on_similar(message: Message, state: FSMContext) -> None:
    q = (message.text or "").strip()
    if q.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "↩️ <b>No Problem</b>\n"
            "<i>Your request has been set aside — nothing was charged. Come back any "
            "time for more reads like the ones you love.</i>")
        return
    data = await state.get_data()
    currency = data.get("currency", "BGM")
    await state.clear()
    uid = message.chat.id
    notice = await message.answer(
        f"📚 <b>Finding reads like “{escape(q)}”…</b>\n"
        "<i>Your librarian is matching on theme, genre and feel — one moment.</i>")
    await _deliver_or_refund(message, uid, currency, f"similar to {q}",
                             await similar_titles(q), notice)


@router.message(RecFSM.awaiting_mood, F.text)
async def on_mood(message: Message, state: FSMContext) -> None:
    q = (message.text or "").strip()
    if q.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "↩️ <b>No Problem</b>\n"
            "<i>Your request has been set aside — nothing was charged. Return whenever "
            "you'd like reads to match a mood.</i>")
        return
    data = await state.get_data()
    currency = data.get("currency", "BGM")
    await state.clear()
    uid = message.chat.id
    notice = await message.answer(
        f"🎭 <b>Matching the “{escape(q)}” mood…</b>\n"
        "<i>Your librarian is finding books that fit the feeling — one moment.</i>")
    await _deliver_or_refund(message, uid, currency, f"{q} mood",
                             await mood_titles(q), notice)


async def _send_batch(message: Message, uid: int) -> None:
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"rec_titles": 1, "rec_sent": 1, "rec_genre": 1}) or {}
    titles = u.get("rec_titles") or []
    sent = int(u.get("rec_sent") or 0)
    genre = u.get("rec_genre") or "your"
    batch = titles[sent:sent + _BATCH]
    if not batch:
        await message.answer(
            "📕 <b>That's The Full Shelf</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>You've reached the end of this curated list — every title "
            "delivered. Hope a few have already found their way onto your "
            "to-read pile.</blockquote>\n"
            "<i>💡 Hungry for more? Start a fresh genre and we'll curate again.</i>",
            reply_markup=kb([btn("🔄 New Genre", "lib_recommend", style="success")]))
        return
    lines = "\n".join(f"<code>{sent + i + 1:>3}.</code> {escape(t)}" for i, t in enumerate(batch))
    await db.safe_update("users", {"user_id": uid}, {"$set": {"rec_sent": sent + len(batch)}})
    more = (sent + len(batch)) < len(titles)
    # Every title is a tappable button: tapping searches the archive and fetches the
    # file (or offers a manual Request-an-Admin if it isn't stocked).
    rows = [[btn(f"📥 {sent + i + 1}. {t[:32]}", f"rget:{sent + i}", style="success")]
            for i, t in enumerate(batch)]
    if more:
        rows.append([btn("🔄 Get More", "rec_more", style="success")])
    rows.append([btn("🛑 End Session", "rec_end", style="danger")])
    await message.answer(
        f"📖 <b>{escape(genre.title())}</b>\n"
        f"<i>Curated for you · picks {sent + 1}–{sent + len(batch)}</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote expandable>{lines}</blockquote>\n"
        "<i>💡 Tap any title below to pull it straight from the library.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("rget:"))
async def cb_rget(call: CallbackQuery, state: FSMContext) -> None:
    """Tapped a recommended title → search the archive and fetch it (or offer a
    manual admin request when it isn't stocked)."""
    await call.answer()
    try:
        i = int(call.data.split(":", 1)[1])
    except ValueError:
        return
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": call.from_user.id},
                                 {"rec_titles": 1}) or {}
    titles = u.get("rec_titles") or []
    if i < 0 or i >= len(titles):
        await call.answer("That pick has rolled off your list — start a fresh recommendation.", show_alert=True)
        return
    from handlers.request import find_in_library
    await find_in_library(call.message, state, titles[i], edit=False)


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
    await call.answer("Session closed — your picks are yours to keep. Happy reading!")
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": call.from_user.id},
                         {"$set": {"rec_titles": [], "rec_sent": 0, "rec_genre": ""}})
    await call.message.edit_text(
        "✨ <b>Session Closed</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>That's a wrap on this list — your recommendations are yours to "
        "keep. Whenever you're ready for the next read, your librarian is just a tap "
        "away.</blockquote>\n"
        "<i>💡 Happy reading — and come back any time for fresh picks.</i>",
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
    from utils.flags import is_on
    if not await is_on("summaries") or not await ai_enabled():
        await message.answer(
            "🤖 <b>Your Librarian Is Off Duty</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>AI summaries are paused at the moment, so on-demand book "
            "briefings aren't available just now. The rest of your library stays open — "
            "search any title or browse Discover in the meantime.</blockquote>\n"
            "<i>💡 Admins can re-enable AI from /admin.</i>")
        return
    bgm, bcn = await get_balances(uid)
    cost = await _ai_cost()
    if bgm + bcn < cost:
        await message.answer(
            "🔒 <b>Not Quite Enough To Begin</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>A book summary costs <code>"
            f"{fmt_amount(cost)}</code> BCN or BGM, and your wallet is a little short "
            "right now.</blockquote>\n"
            "<i>💡 Claim your free daily 🪙 BCN or top up 💎 BGM, then your librarian "
            "will brief you in seconds.</i>")
        return
    await message.answer(
        "📝 <b>AI Book Summary</b>\n"
        "<i>A clear briefing before you commit a single chapter.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Name any book and your librarian returns a crisp, spoiler-light "
        "brief so you can decide if it's the right next read:\n\n"
        "📖 <b>Overview</b> — what the book is really about\n"
        "🎭 <b>Themes</b> — the ideas it explores\n"
        "👤 <b>Best for</b> — the readers who'll love it\n"
        "✨ <b>Takeaways</b> — what you'll walk away with</blockquote>\n"
        f"💎 <b>Cost:</b> <code>{fmt_amount(cost)}</code> BCN / BGM\n"
        f"💼 <b>Your balance:</b> <code>{bcn:.2f}</code> BCN · <code>{bgm:.2f}</code> BGM\n"
        "<i>💡 Spend dries up your free 🪙 BCN first, so your 💎 BGM stays untouched.</i>",
        reply_markup=kb([btn("🚀 Proceed & Pay", "sum_proceed", style="success")],
                        [btn("🔙 Back", "menu_library", style="danger")]))


@router.callback_query(F.data == "sum_proceed")
async def cb_sum_proceed(call: CallbackQuery, state: FSMContext) -> None:
    uid = call.from_user.id
    currency = await spend(uid, await _ai_cost())
    if not currency:
        await call.answer("Your wallet's a touch short — claim your daily BCN or top up BGM to begin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(RecFSM.awaiting_summary_title)
    await state.update_data(currency=currency)
    await call.message.edit_text(
        "✍️ <b>Which Book Shall We Brief?</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the title and your librarian will prepare a spoiler-light "
        "summary in seconds.\n\n"
        "Include the author where you can — it helps us brief the right edition.</blockquote>\n"
        "<i>💡 Changed your mind? Tap Cancel below.</i>",
        reply_markup=kb(cancel_row("menu_library")))


@router.message(RecFSM.awaiting_summary_title, F.text)
async def on_summary_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if title.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "↩️ <b>No Problem</b>\n"
            "<i>Your summary request has been set aside — nothing was charged. Return "
            "whenever you'd like a book briefed.</i>")
        return
    data = await state.get_data()
    currency = data.get("currency", "BGM")
    await state.clear()
    uid = message.chat.id
    notice = await message.answer(
        f"📝 <b>Briefing “{escape(title)}”…</b>\n"
        "<i>Your librarian is reading the room — overview, themes and takeaways "
        "incoming.</i>")
    summary = await summarize_book(title)
    if not summary:
        refund_amt = round(await _ai_cost() * (0.75 if currency == "BCN" else 0.9), 3)
        await refund(uid, refund_amt, "BGM")
        await notice.edit_text(
            f"🤔 <b>Couldn't Find “{escape(title)}”</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your librarian didn't recognise that one well enough to brief "
            "it, so nothing was prepared — and we've returned the spend in full: "
            f"<b><code>{fmt_amount(refund_amt)}</code> BGM</b> back in your "
            "wallet.</blockquote>\n"
            "<i>💡 Try the exact title with the author — the precise spelling helps us "
            "find the right book.</i>")
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid}, {"$set": {"sum_title": title}})
    await notice.edit_text(
        f"📘 <b>{escape(title)}</b>\n"
        "<i>Your librarian's briefing</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote expandable>{summary}</blockquote>\n"
        "<i>💡 Sounds like your next read? Pull it straight from the library below.</i>",
        reply_markup=kb([btn(f"📥 Find «{title[:24]}» in Library", "sum_find", style="success")],
                        [btn("📝 Another Summary", "lib_summary", style="success")],
                        [btn("🔙 Library", "menu_library", style="danger")]))


@router.callback_query(F.data == "sum_find")
async def cb_sum_find(call: CallbackQuery, state: FSMContext) -> None:
    """Fetch the just-summarised title straight from the archive."""
    await call.answer()
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": call.from_user.id},
                                 {"sum_title": 1}) or {}
    title = u.get("sum_title")
    if not title:
        await call.answer("Run a summary first, then I can pull that title for you.", show_alert=True)
        return
    from handlers.request import find_in_library
    await find_in_library(call.message, state, title, edit=False)
