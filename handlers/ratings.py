"""
handlers/ratings.py — rate & review individual titles.

From a favorite's actions: ⭐ Rate → pick 1–5 stars → optionally add a written
review. 📊 Reviews shows the average + recent reviews for that title.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from utils.files import get_file
from utils.keyboards import btn, kb
from utils.ratings import (recent_reviews, set_rating, set_review, stars_bar,
                           summary, user_rating)

logger = logging.getLogger(__name__)
router = Router()


class RateFSM(StatesGroup):
    review = State()


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(call: CallbackQuery) -> None:
    await call.answer()
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    name = (f or {}).get("name", "this book")
    mine = await user_rating(call.from_user.id, fuid)
    cur = f"\nYour current rating: {'⭐' * int(mine['stars'])}" if mine else ""
    await call.message.edit_text(
        f"⭐ <b>Rate</b> <i>{name[:60]}</i>{cur}\n\nHow many stars?",
        reply_markup=kb([btn(f"{n}⭐", f"rate_set:{fuid}:{n}", style="primary") for n in (1, 2, 3)],
                        [btn(f"{n}⭐", f"rate_set:{fuid}:{n}", style="primary") for n in (4, 5)],
                        [btn("📊 See Reviews", f"revw:{fuid}", style="primary")],
                        [btn("🔙 Favorites", "lib_favorites", style="danger")]))


@router.callback_query(F.data.startswith("rate_set:"))
async def cb_rate_set(call: CallbackQuery) -> None:
    _, fuid, n = call.data.split(":")
    n = max(1, min(5, int(n)))
    f = await get_file(fuid)
    await set_rating(call.from_user.id, fuid, n, name=(f or {}).get("name", ""))
    await call.answer(f"Rated {n}⭐ — thanks!")
    avg, count = await summary(fuid)
    await call.message.edit_text(
        f"✅ <b>You rated it {'⭐' * n}</b>\n\n"
        f"📊 Average: {stars_bar(avg)} <b>{avg:g}</b> ({count} rating{'s' if count != 1 else ''})\n\n"
        "Want to add a few words?",
        reply_markup=kb([btn("✍️ Write a Review", f"rate_rev:{fuid}", style="success")],
                        [btn("📊 See Reviews", f"revw:{fuid}", style="primary"),
                         btn("🔙 Favorites", "lib_favorites", style="danger")]))


@router.callback_query(F.data.startswith("rate_rev:"))
async def cb_rate_rev(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    fuid = call.data.split(":", 1)[1]
    await state.set_state(RateFSM.review)
    await state.update_data(fuid=fuid)
    await call.message.edit_text("✍️ <b>Write your review</b> (a sentence or two). "
                                 "/cancel to skip.")


@router.message(RateFSM.review, F.text)
async def on_review(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    data = await state.get_data()
    fuid = data.get("fuid")
    await state.clear()
    if not fuid:
        return
    await set_review(message.chat.id, fuid, raw)
    await message.answer("✅ <b>Review saved</b> — thanks for helping other readers!",
                         reply_markup=kb([btn("📊 See Reviews", f"revw:{fuid}", style="primary")],
                                         [btn("🔙 Favorites", "lib_favorites", style="danger")]))


async def _reviews_view(uid: int, fuid: str):
    from utils.reactions import REACTIONS, counts as react_counts, user_reaction
    f = await get_file(fuid)
    name = (f or {}).get("name", "this book")
    avg, count = await summary(fuid)
    text = (f"📊 <b>Reviews</b> — <i>{escape(name[:60])}</i>\n━━━━━━━━━━━━━━━━━━\n"
            f"{stars_bar(avg)} <b>{avg:g}</b> from {count} rating{'s' if count != 1 else ''}\n")
    if count:
        revs = await recent_reviews(fuid, limit=5)
        for r in revs:
            who = escape((r.get("name") or "Reader")[:20])
            text += f"\n{'⭐' * int(r.get('stars') or 0)} <b>{who}</b>\n<i>{escape(r.get('review',''))}</i>\n"
        if not revs:
            text += "\n<i>No written reviews yet — be the first!</i>"
    else:
        text += "\n<i>No ratings yet — be the first to rate it!</i>"
    # reactions bar (toggle, one per user)
    rc = await react_counts(fuid)
    mine = await user_reaction(fuid, uid)
    react_row = []
    for i, emo in enumerate(REACTIONS):
        n = rc.get(emo, 0)
        label = f"{emo} {n}" if n else emo
        if emo == mine:
            label = f"• {label}"
        react_row.append(btn(label, f"rx:{fuid}:{i}", style="primary"))
    return text, kb(react_row,
                    [btn("⭐ Rate it", f"rate:{fuid}", style="success")],
                    [btn("🔙 Favorites", "lib_favorites", style="danger")])


@router.callback_query(F.data.startswith("revw:"))
async def cb_reviews(call: CallbackQuery) -> None:
    await call.answer()
    fuid = call.data.split(":", 1)[1]
    text, markup = await _reviews_view(call.from_user.id, fuid)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("rx:"))
async def cb_react(call: CallbackQuery) -> None:
    from utils.reactions import REACTIONS, toggle
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer(); return
    _, fuid, idx = parts
    try:
        emoji = REACTIONS[int(idx)]
    except (ValueError, IndexError):
        await call.answer(); return
    new = await toggle(fuid, call.from_user.id, emoji)
    await call.answer(f"Reacted {emoji}" if new else "Reaction removed")
    text, markup = await _reviews_view(call.from_user.id, fuid)
    await call.message.edit_text(text, reply_markup=markup)
