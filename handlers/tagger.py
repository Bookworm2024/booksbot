"""
handlers/tagger.py — admin: AI genre auto-tagging of the WHOLE archive.

Admin panel → 🏷 Tag Genres → tags EVERY untagged file via the AI engine (the
genre is inferred from the title) in one background run — no more 25-at-a-time
batches. The bot first logs the total file count, then works through every
untagged title, asks the AI for its genre, and auto-writes it to the database;
genre-tagged files power Browse-by-Genre in Discover. Gated on the AI engine
being on (utils.ai). Runs as a background task with a live progress card so the
admin isn't left waiting on a single blocking call.
"""
import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.ai import ai_enabled, classify_genre
from utils.files import archive_count, set_genre, untagged_count, untagged_files
from utils.keyboards import btn, kb
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()

_CHUNK = 50            # how many untagged docs to pull per DB round-trip
_PROGRESS_EVERY = 25   # edit the progress card every N tagged (Telegram edit-rate friendly)
_running = False       # module-level guard so the sweep can't be double-started


async def _tag_all(message: Message) -> None:
    """Background sweep: tag every untagged file in the archive, with a live
    progress card. Resilient — a title the AI can't place is stored as 'Other'
    so it is never re-processed, and any per-title error is skipped. The _running
    guard is ALWAYS released (the whole body is wrapped) so a no-op or an error
    can never wedge the tagger shut."""
    global _running
    try:
        await _tag_all_inner(message)
    finally:
        _running = False


async def _tag_all_inner(message: Message) -> None:
    total = await archive_count()
    remaining = await untagged_count()
    logger.info("Genre sweep started: %d files in archive, %d untagged.", total, remaining)
    if remaining == 0:
        await message.answer(
            "✅ <b>Your library is fully tagged</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>📚 <b>Files in archive</b> · <code>{total}</code>\n"
            "🏷 Every title already carries a genre — Browse-by-Genre in Discover "
            "is ready for readers.</blockquote>")
        return

    note = await message.answer(
        "🏷 <b>Genre tagging started</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Reading every title and sorting your whole library by genre.</i>\n\n"
        "<blockquote>"
        f"📚 <b>Files in archive</b> · <code>{total}</code>\n"
        f"🏷 <b>To tag this run</b> · <code>{remaining}</code>\n"
        "⏳ <b>Tagged so far</b> · <code>0</code>"
        "</blockquote>\n"
        "<i>💡 This runs in the background — you can keep using the bot. We'll "
        "update this card as it goes.</i>")

    placed = 0     # classified into a real genre
    othered = 0    # AI returned "Other"
    errored = 0    # an AI/DB error → stored as "Other" to avoid re-processing
    # A safety bound so a pathological untagged-count that never shrinks (e.g. the DB
    # rejecting writes) can't spin forever; comfortably above any real archive size.
    max_iters = total + _CHUNK + 100
    while max_iters > 0:
        max_iters -= 1
        files = await untagged_files(limit=_CHUNK)
        if not files:
            break
        for f in files:
            try:
                g = await classify_genre(f.get("name", "")) or "Other"
                await set_genre(f["file_unique_id"], g)
                if g == "Other":
                    othered += 1
                else:
                    placed += 1
            except Exception:  # noqa: BLE001 — never let one title kill the sweep
                try:
                    await set_genre(f["file_unique_id"], "Other")  # avoid re-processing
                except Exception:  # noqa: BLE001
                    pass
                errored += 1
            done = placed + othered + errored
            if done % _PROGRESS_EVERY == 0:
                left = await untagged_count()
                try:
                    await note.edit_text(
                        "🏷 <b>Genre tagging in progress…</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "<i>Sorting your whole library by genre — sit back.</i>\n\n"
                        "<blockquote>"
                        f"📚 <b>Files in archive</b> · <code>{total}</code>\n"
                        f"✅ <b>Tagged so far</b> · <code>{done}</code>\n"
                        f"⏳ <b>Still to tag</b> · <code>{left}</code>"
                        "</blockquote>")
                except Exception:  # noqa: BLE001 — "message not modified" / rate limit
                    pass
                await asyncio.sleep(0)  # yield to the event loop

    left = await untagged_count()
    tagged = placed + othered + errored
    logger.info("Genre sweep done: tagged=%d (placed=%d, other=%d, errors=%d), untagged left=%d.",
                tagged, placed, othered, errored, left)
    try:
        await note.edit_text(
            "✨ <b>Genre tagging complete</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"📚 <b>Files in archive</b> · <code>{total}</code>\n"
            f"🏷 <b>Tagged this run</b> · <code>{tagged}</code>\n"
            f"🎯 <b>Placed into a genre</b> · <code>{placed}</code>\n"
            f"🗂 <b>Filed as “Other”</b> · <code>{othered}</code>\n"
            f"⚠️ <b>Errors (filed as Other)</b> · <code>{errored}</code>\n"
            f"📭 <b>Still untagged</b> · <code>{left}</code>"
            "</blockquote>\n"
            + ("<i>That's the whole library — Browse-by-Genre is fully stocked. 🛡</i>"
               if left == 0 else
               "<i>💡 A few slipped through (the run may have been interrupted) — tap "
               "below to finish the rest.</i>"),
            reply_markup=kb([btn("🏷 Tag Remaining", "admin_tag", style="success")]
                            if left else
                            [btn("🔙 Back", "admin_open", style="primary")]))
    except Exception:  # noqa: BLE001
        pass


async def _start_run(message: Message) -> None:
    global _running
    if not await ai_enabled():
        await message.answer(
            "🏷 <b>Genre Tagger</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            "🔒 The tagger runs on the AI engine, and it's currently switched off.\n\n"
            "Turn it on under 🤖 <b>AI Engine</b> in <code>/admin</code>, then come "
            "back and we'll start sorting your library by genre."
            "</blockquote>",
            reply_markup=kb([btn("🤖 Open AI Engine", "admin_ai", style="primary")],
                            [btn("🔙 Back", "admin_open", style="danger")]))
        return
    if _running:
        await message.answer(
            "⏳ <b>Already tagging</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>A genre-tagging sweep is already running in the background. "
            "Watch its progress card above — it'll tag the entire library before it "
            "finishes.</blockquote>")
        return
    _running = True
    # Fire-and-forget so the whole-archive sweep never blocks the handler.
    asyncio.create_task(_tag_all(message))


@router.message(Command("tag_genres"))
async def cmd_tag(message: Message) -> None:
    if not is_super(message.chat.id):
        await message.answer("🔒 <b>Owner only.</b>\n<i>The genre tagger spends the AI budget — reserved for the super admin.</i>")
        return
    await _start_run(message)


@router.callback_query(F.data == "admin_tag")
async def cb_tag(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — the genre tagger spends the AI budget, reserved for the super admin.", show_alert=True)
        return
    await call.answer("🏷 Starting a full-library genre sweep…")
    await _start_run(call.message)
