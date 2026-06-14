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

from config import ADMIN_IDS, ANTHROPIC_API_KEY
from utils.ai import classify_genre
from utils.files import set_genre, untagged_count, untagged_files
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_BATCH = 25


async def _run_batch(message: Message) -> None:
    if not ANTHROPIC_API_KEY:
        await message.answer("🏷 Tagging needs ANTHROPIC_API_KEY set.")
        return
    files = await untagged_files(limit=_BATCH)
    if not files:
        await message.answer("✅ All files are tagged.")
        return
    note = await message.answer(f"🏷 Tagging {len(files)} files…")
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
        f"✅ Tagged <b>{tagged}/{len(files)}</b>. Remaining untagged: <b>{remaining}</b>.",
        reply_markup=kb([btn("🏷 Tag Next Batch", "admin_tag", style="success")]
                        if remaining else
                        [btn("🔙 Back", "admin_open", style="primary")]))


@router.message(Command("tag_genres"))
async def cmd_tag(message: Message) -> None:
    if message.chat.id not in ADMIN_IDS:
        await message.answer("🚫 Access denied.")
        return
    await _run_batch(message)


@router.callback_query(F.data == "admin_tag")
async def cb_tag(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer("Tagging…")
    await _run_batch(call.message)
