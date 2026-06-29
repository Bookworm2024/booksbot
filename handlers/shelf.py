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
from utils.keyboards import btn, cancel_row, kb
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
        "📒 <b>My Shelf</b>\n"
        "<i>The books you've finished and the thoughts you kept — gathered and yours.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"📚 <b>Finished</b> · <code>{fin}</code> book(s) marked done — re-fetch any of them in one tap.\n"
        f"📝 <b>Notes</b> · <code>{notes}</code> book(s) annotated — your highlights and reflections, saved per title."
        "</blockquote>\n"
        "<i>💡 Mark a book finished or jot a note from its 📊 Reviews screen.</i>",
        reply_markup=kb([btn(f"📚 Finished ({fin})", "shelf_finished", style="success"),
                         btn(f"📝 Notes ({notes})", "shelf_notes", style="primary")],
                        [btn("🔙 Back to Library", "menu_library", style="danger")]))


@router.message(Command("shelf"))
async def cmd_shelf(message: Message) -> None:
    await message.answer(
        "📒 <b>My Shelf</b>\n"
        "<i>The books you've finished and the thoughts you kept — gathered and yours.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>📚 <b>Finished</b> — re-fetch any title you've completed, free.\n"
        "📝 <b>Notes</b> — your per-book highlights and reflections, kept tidy.</blockquote>",
        reply_markup=kb([btn("📚 Finished", "shelf_finished", style="success"),
                         btn("📝 Notes", "shelf_notes", style="primary")]))


# ── finished shelf ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "shelf_finished")
async def cb_finished(call: CallbackQuery) -> None:
    await call.answer()
    items = await finished_list(call.from_user.id, 20)
    if not items:
        await call.message.edit_text(
            "📚 <b>Finished Books</b>\n"
            "<i>A record of everything you've read to the last page.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Your finished shelf is empty for now — the first title will "
            "feel especially good here.\n\n"
            "When you reach the end of a book, mark it <b>✅ Finished</b> from its "
            "📊 Reviews screen (open a favorite → <b>⭐ Rate</b> → <b>📊 See Reviews</b>). "
            "It'll live here so you can re-fetch it any time, free.</blockquote>\n"
            "<i>💡 Finished books also fuel your reading streak and stats.</i>",
            reply_markup=kb([btn("🔙 Back to Shelf", "menu_shelf", style="danger")]))
        return
    rows = []
    for f in items:
        ext = (f.get("ext") or "").lower()
        rows.append([btn(f"✅ {icon_for(ext)} {f.get('name','Untitled')[:32]}",
                         f"fin_get:{f['file_unique_id']}", style="primary")])
    rows.append([btn("🔙 Back to Shelf", "menu_shelf", style="danger")])
    await call.message.edit_text(
        f"📚 <b>Finished Books</b> · <code>{len(items)}</code>\n"
        f"<i>Every title you've completed — a quiet milestone shelf.</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Tap any book below to have it re-delivered to your chat — "
        f"re-fetching what you've finished is always free.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("fin_get:"))
async def cb_fin_get(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_finished(uid, fuid)
    if not f:
        await call.answer("⚠️ This title isn't on your Finished shelf anymore — mark it done again to keep it here.", show_alert=True)
        return
    await call.answer("📤 On its way — re-delivering from your Finished shelf…")
    caption = (f"{icon_for(f.get('ext',''))} <b>{escape(f.get('name','Your File') or 'Your File')}</b>"
               "\n\n✅ <i>From your Finished shelf — re-fetched free.</i>")
    src = f.get("chan_id") or await get_file_channel()
    try:
        if src and f.get("msg_id"):
            await call.bot.copy_message(uid, src, f["msg_id"], caption=caption)
        elif f.get("file_id"):
            await call.bot.send_document(uid, f["file_id"], caption=caption)
        else:
            await call.message.answer("⚠️ <b>This title is no longer retrievable.</b>\n<i>The source file has moved on. Search for it again and we'll deliver a fresh copy.</i>")
    except Exception as exc:  # noqa: BLE001
        logger.warning("finished re-delivery failed: %s", exc)
        await call.message.answer("⚠️ <b>We couldn't fetch this one right now.</b>\n<i>A momentary hiccup — please tap to re-fetch it again in a few seconds.</i>")


# ── toggle finished (from the Reviews screen) ─────────────────────────────────
@router.callback_query(F.data.startswith("fin_add:"))
async def cb_fin_add(call: CallbackQuery) -> None:
    fuid = call.data.split(":", 1)[1]
    if await is_finished(call.from_user.id, fuid):
        await unmark_finished(call.from_user.id, fuid)
        await call.answer("📚 Taken off your Finished shelf. Mark it done again whenever you like.")
    else:
        f = await get_file(fuid)
        if not f:
            await call.answer("⚠️ We couldn't find that file. It may have been moved — try searching for it again.", show_alert=True)
            return
        await mark_finished(call.from_user.id, f)
        await call.answer("✅ Added to your Finished shelf — nicely done. Re-fetch it free, any time.")
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
        "📝 <b>Add a Note</b>\n"
        "<i>Capture a thought, a quote, a place to return to.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Send your note as a message — up to <code>{MAX_NOTE_LEN}</code> characters. "
        "It's saved privately against this book, so you can revisit it any time.</blockquote>\n"
        "<i>💡 Tap Cancel below to step away without saving.</i>",
        reply_markup=kb(cancel_row("menu_shelf")))


@router.message(NoteFSM.text, F.text)
async def on_note(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("✅ No problem — nothing was saved. Your notes are untouched."); return
    data = await state.get_data()
    await state.clear()
    fuid = data.get("fuid")
    f = await get_file(fuid) if fuid else None
    if not f or not raw:
        await message.answer("⚠️ <b>Nothing to save.</b>\n<i>That note came through empty — send some text and we'll keep it for you.</i>"); return
    await add_note(message.chat.id, f, raw[:MAX_NOTE_LEN])
    await message.answer(
        "✅ <b>Note saved.</b>\n"
        "<i>Tucked away with this book — it'll be waiting when you return.</i>",
        reply_markup=kb([btn("📝 View notes", f"notes_view:{fuid}", style="primary"),
                         btn("📒 My Shelf", "menu_shelf", style="primary")]))


@router.callback_query(F.data == "shelf_notes")
async def cb_notes(call: CallbackQuery) -> None:
    await call.answer()
    books = await books_with_notes(call.from_user.id)
    if not books:
        await call.message.edit_text(
            "📝 <b>Your Notes</b>\n"
            "<i>Every thought you keep, organised by the book it belongs to.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>No notes just yet — your reading journal starts the moment "
            "you write your first line.\n\n"
            "Add a note from any book's 📊 Reviews screen: a favourite quote, a chapter "
            "to revisit, a reaction worth remembering.</blockquote>\n"
            "<i>💡 Notes stay private and are grouped per title automatically.</i>",
            reply_markup=kb([btn("🔙 Back to Shelf", "menu_shelf", style="danger")]))
        return
    rows = [[btn(f"📝 {escape(b['name'][:30])} ({b['count']})",
                 f"notes_view:{b['fuid']}", style="primary")] for b in books[:15]]
    rows.append([btn("🔙 Back to Shelf", "menu_shelf", style="danger")])
    await call.message.edit_text(
        "📝 <b>Your Notes</b>\n"
        "<i>Your reading journal — gathered by book, kept just for you.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Choose a title below to read, add to, or tidy up its notes.</blockquote>",
        reply_markup=kb(*rows))


async def _notes_screen(uid: int, fuid: str):
    notes = await notes_for(uid, fuid)
    if not notes:
        return ("📝 <b>No notes for this book yet</b>\n"
                "<i>A blank page, waiting for your first thought.</i>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>Tap <b>➕ Add Note</b> to capture a quote, a reaction, or a "
                "place worth returning to. It'll be saved privately against this title.</blockquote>",
                kb([btn("➕ Add a note", f"note_add:{fuid}", style="success")],
                   [btn("🔙 Back to Notes", "shelf_notes", style="danger")]))
    name = escape((notes[0].get("name") or "this book")[:50])
    lines = [f"📝 <b>Notes — {name}</b>",
             "<i>Your highlights and reflections for this title.</i>",
             "━━━━━━━━━━━━━━━━━━", "<blockquote>"]
    rows = []
    for n in notes[:10]:
        lines.append(f"• {escape(n.get('text',''))}")
        rows.append([btn(f"🗑 Delete: {n.get('text','')[:18]}", f"note_del:{n['note_id']}",
                         style="danger")])
    lines.append("</blockquote>")
    rows.append([btn("➕ Add a note", f"note_add:{fuid}", style="success")])
    rows.append([btn("🔙 Back to Notes", "shelf_notes", style="danger")])
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
    await call.answer("🗑 Note deleted. The rest of your notes for this book are untouched.")
    fuid = (db_note or {}).get("file_unique_id")
    if fuid:
        text, markup = await _notes_screen(call.from_user.id, fuid)
        await call.message.edit_text(text, reply_markup=markup)
    else:
        await cb_notes(call)
