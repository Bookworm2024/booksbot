"""
handlers/tagger.py — admin: AI genre auto-tagging of the archive.

Admin panel → 🏷 Tag Genres → tags a batch of untagged files via Claude (the
genre is inferred from the title). Run repeatedly to chew through the archive;
genre-tagged files power Browse-by-Genre in Discover. Gated on ANTHROPIC_API_KEY.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from utils.ai import ai_enabled, classify_genre
from utils.files import set_genre, untagged_count, untagged_files
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_BATCH = 25


async def _run_batch(message: Message) -> None:
    if not await ai_enabled():
        await message.answer(
            "🏷 <b>Genre Tagger</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            "🔒 The tagger runs on the AI engine, and it's currently switched off.\n\n"
            "Turn it on under 🤖 <b>AI Engine</b> in <code>/admin</code>, then come "
            "back and we'll start sorting your library by genre."
            "</blockquote>")
        return
    files = await untagged_files(limit=_BATCH)
    if not files:
        await message.answer(
            "✅ <b>Your library is fully tagged</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Every file carries a genre — Browse-by-Genre in Discover is ready for readers.</i>")
        return
    note = await message.answer(
        "🏷 <b>Tagging in progress…</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Reading titles and sorting <code>{len(files)}</code> files by genre. One moment.</i>")
    tagged = 0
    for f in files:
        g = await classify_genre(f.get("name", ""))
        if g:
            await set_genre(f["file_unique_id"], g)
            tagged += 1
        else:
            await set_genre(f["file_unique_id"], "Other")  # avoid re-processing
    remaining = await untagged_count()
    await note.edit_text(
        "✨ <b>Batch complete</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🏷 <b>Tagged this run</b> · <code>{tagged}/{len(files)}</code>\n"
        f"📚 <b>Still untagged</b> · <code>{remaining}</code>"
        "</blockquote>\n"
        + ("<i>💡 Keep going — run the next batch to clear the rest.</i>"
           if remaining else
           "<i>That was the last of them — your whole library is now genre-sorted.</i>"),
        reply_markup=kb([btn("🏷 Tag Next Batch", "admin_tag", style="success")]
                        if remaining else
                        [btn("🔙 Back", "admin_open", style="primary")]))


@router.message(Command("tag_genres"))
async def cmd_tag(message: Message) -> None:
    if message.chat.id not in ADMIN_IDS:
        await message.answer("🔒 <b>Admins only.</b>\n<i>The genre tagger is part of the admin toolkit.</i>")
        return
    await _run_batch(message)


@router.callback_query(F.data == "admin_tag")
async def cb_tag(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("🔒 Admins only — the genre tagger is part of the admin toolkit.", show_alert=True)
        return
    await call.answer("🏷 Sorting the next batch by genre…")
    await _run_batch(call.message)
