"""
handlers/shelf.py — "Books Finished" shelf + per-book notes (Reader).

Library → 📒 My Shelf:
  📚 Finished — books you've marked done (one-tap re-fetch)
  📝 Notes    — your per-book notes

Per-title actions (✅ Finished / 📝 Note) live on the 📊 Reviews screen, which is
reachable from a favorite's ⭐ Rate flow.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from utils.channel import get_file_channel
from utils.files import get_file, icon_for
from utils.keyboards import btn, kb
from utils.shelf import (
    MAX_NOTE_LEN, add_note, books_with_notes, delete_note, finished_count,
    finished_list, get_finished, is_finished, mark_finished, notes_for,
    unmark_finished,
)

logger = logging.getLogger(__name__)
router = Router()


class NoteFSM(StatesGroup):
    text = State()


# ── shelf hub ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu_shelf")
async def cb_shelf(call: CallbackQuery) -> None:
    await call.answer()
    fin = await finished_count(call.from_user.id)
    notes = len(await books_with_notes(call.from_user.id))
    await call.message.edit_text(
        "<b>📒 My Shelf</b>\n━━━━━━━━━━━━━━━━━━\nYour finished books and notes.",
        reply_markup=kb([btn(f"📚 Finished ({fin})", "shelf_finished", style="success"),
                         btn(f"📝 Notes ({notes})", "shelf_notes", style="primary")],
                        [btn("🔙 Library", "menu_library", style="danger")]))


@router.message(Command("shelf"))
async def cmd_shelf(message: Message) -> None:
    await message.answer(
        "<b>📒 My Shelf</b>\nYour finished books and notes.",
        reply_markup=kb([btn("📚 Finished", "shelf_finished", style="success"),
                         btn("📝 Notes", "shelf_notes", style="primary")]))


# ── finished shelf ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "shelf_finished")
async def cb_finished(call: CallbackQuery) -> None:
    await call.answer()
    items = await finished_list(call.from_user.id, 20)
    if not items:
        await call.message.edit_text(
            "📚 <b>Finished Shelf empty</b>\n\nMark a book finished from its 📊 Reviews "
            "screen (open a favorite → ⭐ Rate → 📊 See Reviews).",
            reply_markup=kb([btn("🔙 Shelf", "menu_shelf", style="danger")]))
        return
    rows = []
    for f in items:
        ext = (f.get("ext") or "").lower()
        rows.append([btn(f"✅ {icon_for(ext)} {f.get('name','Untitled')[:32]}",
                         f"fin_get:{f['file_unique_id']}", style="primary")])
    rows.append([btn("🔙 Shelf", "menu_shelf", style="danger")])
    await call.message.edit_text(
        f"📚 <b>Finished Books</b> · {len(items)}\nTap to re-fetch:", reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("fin_get:"))
async def cb_fin_get(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_finished(uid, fuid)
    if not f:
        await call.answer("Not on your shelf.", show_alert=True)
        return
    await call.answer("📤 Sending…")
    caption = (f"{icon_for(f.get('ext',''))} <b>{escape(f.get('name','Your File') or 'Your File')}</b>"
               "\n\n✅ From your Finished shelf")
    src = f.get("chan_id") or await get_file_channel()
    try:
        if src and f.get("msg_id"):
            await call.bot.copy_message(uid, src, f["msg_id"], caption=caption)
        elif f.get("file_id"):
            await call.bot.send_document(uid, f["file_id"], caption=caption)
        else:
            await call.message.answer("❌ This file is no longer retrievable.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("finished re-delivery failed: %s", exc)
        await call.message.answer("❌ Couldn't retrieve this file right now.")


# ── toggle finished (from the Reviews screen) ─────────────────────────────────
@router.callback_query(F.data.startswith("fin_add:"))
async def cb_fin_add(call: CallbackQuery) -> None:
    fuid = call.data.split(":", 1)[1]
    if await is_finished(call.from_user.id, fuid):
        await unmark_finished(call.from_user.id, fuid)
        await call.answer("Removed from Finished.")
    else:
        f = await get_file(fuid)
        if not f:
            await call.answer("File not found.", show_alert=True)
            return
        await mark_finished(call.from_user.id, f)
        await call.answer("✅ Marked finished!")
    # re-render the reviews screen to reflect the new state
    from handlers.ratings import _reviews_view
    text, markup = await _reviews_view(call.from_user.id, fuid)
    await call.message.edit_text(text, reply_markup=markup)


# ── notes ─────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("note_add:"))
async def cb_note_add(call: CallbackQuery, state: FSMContext) -> None:
    fuid = call.data.split(":", 1)[1]
    await call.answer()
    await state.set_state(NoteFSM.text)
    await state.update_data(fuid=fuid)
    await call.message.answer(
        f"📝 <b>Add a note</b> (max {MAX_NOTE_LEN} chars). Send your note, or /cancel.")


@router.message(NoteFSM.text, F.text)
async def on_note(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    data = await state.get_data()
    await state.clear()
    fuid = data.get("fuid")
    f = await get_file(fuid) if fuid else None
    if not f or not raw:
        await message.answer("Nothing saved."); return
    await add_note(message.chat.id, f, raw[:MAX_NOTE_LEN])
    await message.answer(
        "✅ <b>Note saved.</b>",
        reply_markup=kb([btn("📝 View Notes", f"notes_view:{fuid}", style="primary"),
                         btn("📒 Shelf", "menu_shelf", style="primary")]))


@router.callback_query(F.data == "shelf_notes")
async def cb_notes(call: CallbackQuery) -> None:
    await call.answer()
    books = await books_with_notes(call.from_user.id)
    if not books:
        await call.message.edit_text(
            "📝 <b>No notes yet</b>\n\nAdd a note from a book's 📊 Reviews screen.",
            reply_markup=kb([btn("🔙 Shelf", "menu_shelf", style="danger")]))
        return
    rows = [[btn(f"📝 {escape(b['name'][:30])} ({b['count']})",
                 f"notes_view:{b['fuid']}", style="primary")] for b in books[:15]]
    rows.append([btn("🔙 Shelf", "menu_shelf", style="danger")])
    await call.message.edit_text("📝 <b>Your Notes</b>\nPick a book:", reply_markup=kb(*rows))


async def _notes_screen(uid: int, fuid: str):
    notes = await notes_for(uid, fuid)
    if not notes:
        return ("📝 No notes for this book.",
                kb([btn("➕ Add Note", f"note_add:{fuid}", style="success")],
                   [btn("🔙 Notes", "shelf_notes", style="danger")]))
    name = escape((notes[0].get("name") or "this book")[:50])
    lines = [f"📝 <b>Notes — {name}</b>", "━━━━━━━━━━━━━━━━━━"]
    rows = []
    for n in notes[:10]:
        lines.append(f"• {escape(n.get('text',''))}")
        rows.append([btn(f"🗑 Delete: {n.get('text','')[:18]}", f"note_del:{n['note_id']}",
                         style="danger")])
    rows.append([btn("➕ Add Note", f"note_add:{fuid}", style="success")])
    rows.append([btn("🔙 Notes", "shelf_notes", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.startswith("notes_view:"))
async def cb_notes_view(call: CallbackQuery) -> None:
    await call.answer()
    fuid = call.data.split(":", 1)[1]
    text, markup = await _notes_screen(call.from_user.id, fuid)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("note_del:"))
async def cb_note_del(call: CallbackQuery) -> None:
    from database.connection import MongoManager
    note_id = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    db_note = await db.find_one_global(
        "notes", {"user_id": call.from_user.id, "note_id": note_id}, {"file_unique_id": 1})
    await delete_note(call.from_user.id, note_id)
    await call.answer("🗑 Deleted")
    fuid = (db_note or {}).get("file_unique_id")
    if fuid:
        text, markup = await _notes_screen(call.from_user.id, fuid)
        await call.message.edit_text(text, reply_markup=markup)
    else:
        await cb_notes(call)
