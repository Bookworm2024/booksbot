"""
handlers/tbr.py — Reading List (Wishlist / To-Be-Read).

Tap 📌 on a search result to save a book to read later. 📖 Library → 📌 Reading
List shows them; tapping a title downloads it (and removes it from the list).
Distinct from ⭐ Favorites (already-downloaded) and the Watchlist (not-yet-in-archive).
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.files import get_file, icon_for
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_PER = 10


@router.callback_query(F.data.startswith("tbr_add:"))
async def cb_add(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("File not found.", show_alert=True)
        return
    db = await MongoManager.get()
    if await db.find_one_global("tbr", {"user_id": uid, "file_unique_id": fuid}):
        await call.answer("Already on your Reading List 📌", show_alert=True)
        return
    await db.safe_insert("tbr", {
        "user_id": uid, "file_unique_id": fuid, "name": f.get("name"),
        "ext": f.get("ext"), "added_at": datetime.now(timezone.utc)})
    await call.answer("📌 Saved to your Reading List!")


@router.message(Command("tbr"))
async def cmd_tbr(message: Message) -> None:
    await _render(message, message.chat.id, edit=False)


@router.callback_query(F.data == "lib_tbr")
async def cb_tbr(call: CallbackQuery) -> None:
    await call.answer()
    await _render(call.message, call.from_user.id, edit=True)


async def _render(message: Message, uid: int, *, edit: bool) -> None:
    db = await MongoManager.get()
    items = await db.find_global("tbr", {"user_id": uid}, sort=[("added_at", -1)], limit=200)
    send = message.edit_text if edit else message.answer
    if not items:
        await send("📌 <b>Reading List is empty</b>\n\nTap 📌 on any search result to "
                   "save a book to read later.",
                   reply_markup=kb([btn("🔍 Find Books", "req_auto", style="success")],
                                   [btn("🔙 Library", "menu_library", style="danger")]))
        return
    rows = []
    for f in items[:_PER]:
        fuid = f["file_unique_id"]
        ext = (f.get("ext") or "").lower()
        rows.append([btn(f"{icon_for(ext)} {f.get('name', 'Untitled')[:30]}", f"dl:{fuid}",
                         style="success"),
                     btn("🗑", f"tbr_del:{fuid}", style="danger")])
    rows.append([btn("🔙 Library", "menu_library", style="danger")])
    more = f" (showing first {_PER})" if len(items) > _PER else ""
    await send(f"📌 <b>Reading List</b> — {len(items)} book(s){more}\n"
               "Tap a title to download it (it leaves the list once you do).",
               reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("tbr_del:"))
async def cb_del(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    for idx in db.healthy:
        await db.dbs[idx]["tbr"].delete_one({"user_id": uid, "file_unique_id": fuid})
    await call.answer("🗑 Removed")
    await _render(call.message, uid, edit=True)
