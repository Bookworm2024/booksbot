"""
handlers/fallback.py — last-resort handler for stray text.

Included LAST (after every command/state handler, before the channel_post
indexer). It catches a free-text message ONLY when the user is in no FSM flow
(StateFilter(None)) and it isn't a command — i.e. someone typing into the chat
with nothing else listening. Instead of dead air (or, after the old stuck-state
bug, a misleading "no matches found"), it points them at the search flow.

Because it is StateFilter(None), it never fires during gift/redeem/search/admin
prompts; because it ignores '/' it never shadows a command. So it can't trap a
user and can't swallow another handler's input.
"""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from utils.keyboards import btn, kb

router = Router()


@router.message(StateFilter(None), F.text)
async def on_stray_text(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    if txt.startswith("/"):
        return  # unknown command — stay quiet, don't nag
    await message.answer(
        "🤔 <b>Looking for a book?</b>\n\n"
        "Tap <b>📚 Request a Book</b> and send me the title — I'll search the "
        "archive for it.",
        reply_markup=kb([btn("📚 Request a Book", "req_auto", style="success")],
                        [btn("🏠 Menu", "menu_home", style="primary")]))
