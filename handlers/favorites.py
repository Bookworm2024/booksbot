"""
handlers/favorites.py — personal saved-files collection.

  ⭐ Add to Favorites (from a delivered file or search result)
  📖 My Library → Favorites → paginated list → view (free re-delivery) / remove

Re-delivering a favorite is FREE (the user already paid once), routed through
copy_message from the archive channel like the paid path.
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import FILE_CHANNEL_ID
from database.connection import MongoManager
from utils.files import get_file, icon_for
from utils.keyboards import btn, kb, webapp_btn

_AUDIO_EXT = {"mp3", "m4b", "m4a", "wav", "ogg", "flac", "aac"}

logger = logging.getLogger(__name__)
router = Router()

_PER_PAGE = 6


@router.callback_query(F.data.startswith("fav_add:"))
async def cb_fav_add(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("File not found.", show_alert=True)
        return
    db = await MongoManager.get()
    if await db.find_one_global("favorites", {"user_id": uid, "file_unique_id": fuid}):
        await call.answer("Already in your favorites ⭐", show_alert=True)
        return
    await db.safe_insert("favorites", {
        "user_id": uid, "file_unique_id": fuid,
        "name": f.get("name"), "ext": f.get("ext"), "kind": f.get("kind"),
        "msg_id": f.get("msg_id"), "file_id": f.get("file_id"),
        "added_at": datetime.now(timezone.utc),
    })
    await call.answer("⭐ Added to favorites!")


@router.callback_query(F.data == "lib_favorites")
async def cb_favorites(call: CallbackQuery) -> None:
    await call.answer()
    await _render(call, 0)


@router.callback_query(F.data.startswith("fav_pg:"))
async def cb_fav_page(call: CallbackQuery) -> None:
    await call.answer()
    await _render(call, int(call.data.split(":", 1)[1]))


async def _render(call: CallbackQuery, page: int) -> None:
    uid = call.from_user.id
    db = await MongoManager.get()
    items = await db.find_global("favorites", {"user_id": uid},
                                 sort=[("added_at", -1)])
    if not items:
        await call.message.edit_text(
            "⭐ <b>Favorites Empty</b>\n\nTap “Add to Favorites” on any file to save it here.",
            reply_markup=kb([btn("🔙 Back", "menu_library", style="danger")]))
        return

    total = len(items)
    pages = (total + _PER_PAGE - 1) // _PER_PAGE
    page = max(0, min(page, pages - 1))
    chunk = items[page * _PER_PAGE:(page + 1) * _PER_PAGE]

    rows = []
    for f in chunk:
        fuid = f["file_unique_id"]
        ext = (f.get("ext") or "").lower()
        name = f.get("name", "Untitled")[:32]
        is_audio = f.get("kind") == "audio" or ext in _AUDIO_EXT
        # title row
        rows.append([btn(f"{icon_for(ext)} {name}", f"fav_get:{fuid}", style="primary")])
        # action row: open in the universal viewer (routes by type) · chat · remove
        open_btn = webapp_btn(
            "🎧 Listen" if is_audio else "📖 Open",
            "view.html", query=f"fuid={fuid}&ext={ext}",
            style="success", fallback_cb=f"fav_get:{fuid}")
        rows.append([open_btn,
                     btn("📥 Chat", f"fav_get:{fuid}", style="primary"),
                     btn("🗑", f"fav_del:{fuid}", style="danger")])
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev", f"fav_pg:{page-1}", style="primary"))
    if page + 1 < pages:
        nav.append(btn("Next ➡️", f"fav_pg:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 Back", "menu_library", style="danger")])

    await call.message.edit_text(
        f"⭐ <b>Your Favorites</b>\n📚 {total} saved · page {page + 1}/{pages}",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("fav_get:"))
async def cb_fav_get(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    f = await db.find_one_global("favorites", {"user_id": uid, "file_unique_id": fuid})
    if not f:
        await call.answer("Not in your favorites.", show_alert=True)
        return
    await call.answer("📤 Sending…")
    caption = f"{icon_for(f.get('ext',''))} <b>{f.get('name','Your File')}</b>\n\n⭐ From your favorites"
    try:
        if FILE_CHANNEL_ID and f.get("msg_id"):
            await call.bot.copy_message(uid, FILE_CHANNEL_ID, f["msg_id"], caption=caption)
        elif f.get("file_id"):
            await call.bot.send_document(uid, f["file_id"], caption=caption)
        else:
            await call.message.answer("❌ This file is no longer retrievable.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Favorite re-delivery failed: %s", exc)
        await call.message.answer("❌ Couldn't retrieve this file right now.")


@router.callback_query(F.data.startswith("fav_del:"))
async def cb_fav_del(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    for idx in db.healthy:
        await db.dbs[idx]["favorites"].delete_one({"user_id": uid, "file_unique_id": fuid})
    await call.answer("🗑 Removed")
    await _render(call, 0)
