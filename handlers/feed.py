"""
handlers/feed.py — 🎯 For You: personalized picks.

📖 Library → 🎯 For You: books chosen from the user's most-loved genre (from
their favorites), falling back to fresh New Arrivals. Already-favorited titles
are excluded so picks always feel new.
"""
import logging
from collections import Counter

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.connection import MongoManager
from utils.files import files_by_genre, icon_for, recent_files
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "lib_foryou")
async def cb_foryou(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    db = await MongoManager.get()
    fav_rows = await db.find_global("favorites", {"user_id": uid}, limit=200,
                                    proj={"file_unique_id": 1})
    owned = {x["file_unique_id"] for x in fav_rows}
    picks, basis = [], "New Arrivals"

    if owned:
        files = await db.find_global("files", {"file_unique_id": {"$in": list(owned)[:200]}},
                                     proj={"genre": 1})
        genres = Counter(f.get("genre") for f in files if f.get("genre"))
        if genres:
            top = genres.most_common(1)[0][0]
            basis = f"your love of <b>{top}</b>"
            picks = [f for f in await files_by_genre(top, limit=24)
                     if f["file_unique_id"] not in owned][:8]
    if not picks:
        picks = [f for f in await recent_files(limit=24) if f["file_unique_id"] not in owned][:8]

    if not picks:
        await call.message.edit_text(
            "🎯 <b>For You</b>\n"
            "<i>Your personal shelf, curated by us.</i>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your taste profile is still a blank page — and that's "
            "the best part. ✨\n\n"
            "🔖 <b>Favourite a few titles</b> you love, and we'll read between "
            "the lines to hand-pick books made just for you.\n"
            "🔭 <b>Or open Discover</b> to wander New Arrivals, the Book of the "
            "Day and curated shelves until something clicks.</blockquote>\n"
            "<i>💡 The more you save, the sharper your picks become.</i>",
            reply_markup=kb([btn("🔭 Explore Discover", "lib_discover", style="success")],
                            [btn("🔙 Back to Library", "menu_library", style="danger")]))
        return

    rows = [[btn(f"{icon_for(f.get('ext', ''))} {f.get('name', 'Untitled')[:34]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in picks]
    rows.append([btn("🔙 Back to Library", "menu_library", style="danger")])
    await call.message.edit_text(
        "🎯 <b>For You</b>\n"
        "<i>Hand-picked, just for your shelf.</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>We pulled these from {basis} — and left out anything "
        "you've already saved, so every title here is a fresh discovery.\n\n"
        "📖 Tap any book below to open it instantly.</blockquote>\n"
        "<i>💡 Favourite the ones you enjoy — it keeps these picks beautifully "
        "on-point.</i>",
        reply_markup=kb(*rows))
