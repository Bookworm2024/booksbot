"""
handlers/moderation_admin.py — admin auto-moderation panel.

Admin → 🧰 More Tools → 🛡 Auto-Mod: toggle the filter, view/add/remove blocked
terms. Heuristics (too many links, shouting, char-spam) are always on while the
filter is enabled; the word list is the customisable part.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from utils.audit import log_action
from utils.keyboards import btn, kb
from utils.moderation import (
    add_banned, banned_words, is_enabled, remove_banned, set_enabled,
)

logger = logging.getLogger(__name__)
router = Router()


class ModFSM(StatesGroup):
    add_word = State()
    del_word = State()


def _super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


async def _panel():
    on = await is_enabled()
    words = await banned_words()
    shown = ", ".join(escape(w) for w in words[:40]) or "<i>none</i>"
    text = (f"🛡 <b>Auto-Moderation</b> — {'🟢 ON' if on else '🔴 OFF'}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Filters club posts &amp; reviews for spam (links/shouting/repetition) "
            "and blocked terms.\n\n"
            f"<b>Blocked terms ({len(words)}):</b>\n{shown}")
    rows = [
        [btn("🔴 Turn OFF" if on else "🟢 Turn ON", "mod_toggle",
             style="danger" if on else "success")],
        [btn("➕ Add Term", "mod_add", style="success"),
         btn("➖ Remove Term", "mod_del", style="danger")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return text, kb(*rows)


@router.callback_query(F.data == "admin_mod")
async def cb_mod(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "mod_toggle")
async def cb_toggle(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    on = await is_enabled()
    await set_enabled(not on)
    await log_action(call.from_user.id, "moderation", "off" if on else "on")
    await call.answer(f"Auto-Mod {'OFF' if on else 'ON'}")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "mod_add")
async def cb_add(call: CallbackQuery, state: FSMContext) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(ModFSM.add_word)
    await call.message.answer("➕ Send the <b>term/phrase</b> to block. /cancel to abort.")


@router.message(ModFSM.add_word, F.text)
async def on_add(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    added = await add_banned(raw)
    await log_action(message.chat.id, "mod_add", raw[:40])
    text, markup = await _panel()
    await message.answer(("✅ Added." if added else "ℹ️ Already blocked.") + "\n\n" + text,
                         reply_markup=markup)


@router.callback_query(F.data == "mod_del")
async def cb_del(call: CallbackQuery, state: FSMContext) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(ModFSM.del_word)
    await call.message.answer("➖ Send the <b>term</b> to unblock. /cancel to abort.")


@router.message(ModFSM.del_word, F.text)
async def on_del(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    removed = await remove_banned(raw)
    await log_action(message.chat.id, "mod_del", raw[:40])
    text, markup = await _panel()
    await message.answer(("✅ Removed." if removed else "ℹ️ Not in the list.") + "\n\n" + text,
                         reply_markup=markup)
