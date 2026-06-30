"""
handlers/feed.py — 🎯 For You: personalized picks.

📖 Library → 🎯 For You: the bot learns each reader's favourite genre from every
title they read or request (tracked via the AI engine — utils.foryou), then serves
fresh books from that genre and shows how many of their reads/requests matched it.
Already-read / already-saved titles are excluded so picks always feel new; with no
taste profile yet it falls back to New Arrivals.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.connection import MongoManager
from utils.files import files_by_genre, icon_for, recent_files
from utils.foryou import favorite_genre
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "lib_foryou")
async def cb_foryou(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    db = await MongoManager.get()
    fav_rows = await db.find_global("favorites", {"user_id": uid}, limit=300,
                                    proj={"file_unique_id": 1})
    lib_rows = await db.find_global("library", {"user_id": uid}, limit=400,
                                    proj={"file_unique_id": 1})
    owned = ({x["file_unique_id"] for x in fav_rows}
             | {x["file_unique_id"] for x in lib_rows})

    genre, count, total = await favorite_genre(uid)

    # ── personalized view: a known favourite genre ───────────────────────────────
    if genre:
        picks = [f for f in await files_by_genre(genre, limit=40)
                 if f["file_unique_id"] not in owned][:8]
        stat = (f"📊 <b>{count}</b> of your <b>{total}</b> reads &amp; requests "
                f"so far are <b>{escape(genre)}</b>." if total else "")
        if picks:
            from utils import prepare
            cm = await prepare.clean_names_for(picks)
            rows = [[btn(f"{icon_for(f.get('ext', ''))} {(cm.get(f['file_unique_id']) or f.get('name', 'Untitled'))[:34]}",
                         f"dl:{f['file_unique_id']}", style="success")] for f in picks]
            rows.append([btn("🔭 Explore Discover", "lib_discover", style="primary")])
            rows.append([btn("🔙 Back to Library", "menu_library", style="danger")])
            await call.message.edit_text(
                "🎯 <b>For You</b>\n"
                "<i>Your taste, learned from everything you read &amp; request.</i>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>"
                f"📚 <b>Your favourite genre:</b> {escape(genre)}\n"
                f"{stat}\n\n"
                f"Here's more <b>{escape(genre)}</b> you haven't opened yet — "
                "tap any title to read it instantly.</blockquote>",
                reply_markup=kb(*rows))
            return
        # favourite genre known, but every title in it is already read/saved
        await call.message.edit_text(
            "🎯 <b>For You</b>\n"
            "<i>Your taste, learned from everything you read &amp; request.</i>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"📚 <b>Your favourite genre:</b> {escape(genre)}\n"
            f"{stat}\n\n"
            f"You've worked through every <b>{escape(genre)}</b> title we have — "
            "impressive. 🌟 Explore other shelves while we stock more.</blockquote>",
            reply_markup=kb([btn("🔭 Explore Discover", "lib_discover", style="success")],
                            [btn("🔙 Back to Library", "menu_library", style="danger")]))
        return

    # ── no taste profile yet → fresh arrivals (excluding anything owned) ──────────
    picks = [f for f in await recent_files(limit=24) if f["file_unique_id"] not in owned][:8]
    if not picks:
        await call.message.edit_text(
            "🎯 <b>For You</b>\n"
            "<i>Your personal shelf, curated by us.</i>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your taste profile is still a blank page — and that's "
            "the best part. ✨\n\n"
            "📥 <b>Read or request a few titles</b> and the bot learns your "
            "favourite genre, then fills this shelf with more like them.\n"
            "🔭 <b>Or open Discover</b> to wander New Arrivals, the Book of the "
            "Day and curated shelves until something clicks.</blockquote>\n"
            "<i>💡 The more you read, the sharper your picks become.</i>",
            reply_markup=kb([btn("🔭 Explore Discover", "lib_discover", style="success")],
                            [btn("🔙 Back to Library", "menu_library", style="danger")]))
        return

    from utils import prepare
    cm = await prepare.clean_names_for(picks)
    rows = [[btn(f"{icon_for(f.get('ext', ''))} {(cm.get(f['file_unique_id']) or f.get('name', 'Untitled'))[:34]}",
                 f"dl:{f['file_unique_id']}", style="success")] for f in picks]
    rows.append([btn("🔙 Back to Library", "menu_library", style="danger")])
    await call.message.edit_text(
        "🎯 <b>For You</b>\n"
        "<i>Fresh off the shelf while we learn your taste.</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Read or request a few titles and the bot will spot your "
        "favourite genre and tailor this shelf to it.\n\n"
        "📖 For now, here are the newest arrivals — tap any to open it instantly.</blockquote>",
        reply_markup=kb(*rows))
