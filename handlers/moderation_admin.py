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
from utils.keyboards import btn, cancel_row, kb
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
    shown = ", ".join(escape(w) for w in words[:40]) or "<i>none yet — only the built-in spam checks are running</i>"
    status = "🟢 <b>Active</b>" if on else "🔴 <b>Paused</b>"
    text = (f"🛡 <b>Auto-Moderation</b> — {status}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Your always-on guardrail for community spaces.</i>\n\n"
            "<blockquote>Every club post and review is screened automatically for spam — "
            "too many links, all-caps shouting and character flooding — alongside the "
            "blocked-term list you curate below. Clean content passes through instantly; "
            "anything flagged is quietly held back.</blockquote>\n"
            f"<b>Blocked terms</b> · <code>{len(words)}</code> on the list\n"
            f"<blockquote expandable>{shown}</blockquote>\n"
            "<i>💡 Heuristic checks stay on whenever the filter is active — the term list is yours to fine-tune.</i>")
    rows = [
        [btn("🔴 Pause Filter" if on else "🟢 Activate Filter", "mod_toggle",
             style="danger" if on else "success")],
        [btn("➕ Add Blocked Term", "mod_add", style="success"),
         btn("➖ Remove Term", "mod_del", style="danger")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return text, kb(*rows)


@router.callback_query(F.data == "admin_mod")
async def cb_mod(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Reserved for the super admin — moderation controls are locked.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "mod_toggle")
async def cb_toggle(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Reserved for the super admin — moderation controls are locked.", show_alert=True)
        return
    on = await is_enabled()
    await set_enabled(not on)
    await log_action(call.from_user.id, "moderation", "off" if on else "on")
    await call.answer("Filter paused — heuristics and term checks are off." if on
                      else "Filter active — community spaces are protected.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "mod_add")
async def cb_add(call: CallbackQuery, state: FSMContext) -> None:
    if not _super(call.from_user.id):
        await call.answer("Reserved for the super admin — moderation controls are locked.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ModFSM.add_word)
    await call.message.answer(
        "➕ <b>Add a Blocked Term</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the word or phrase you'd like to block. Single words match on "
        "their own; multi-word phrases match anywhere they appear. Casing doesn't matter — "
        "we normalise it for you.</blockquote>\n"
        "<i>💡 Tap Cancel below to step back.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(ModFSM.add_word, F.text)
async def on_add(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ <b>Cancelled.</b>\n<i>The blocked-term list is unchanged.</i>")
        return
    await state.clear()
    added = await add_banned(raw)
    await log_action(message.chat.id, "mod_add", raw[:40])
    text, markup = await _panel()
    head = ("✨ <b>Term added</b> — it's screened from now on.\n\n" if added
            else "ℹ️ <b>Already on the list</b> — no change needed.\n\n")
    await message.answer(head + text, reply_markup=markup)


@router.callback_query(F.data == "mod_del")
async def cb_del(call: CallbackQuery, state: FSMContext) -> None:
    if not _super(call.from_user.id):
        await call.answer("Reserved for the super admin — moderation controls are locked.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ModFSM.del_word)
    await call.message.answer(
        "➖ <b>Remove a Blocked Term</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the exact term you'd like to lift from the list. Once removed, "
        "content using it will pass the term check again — the built-in spam heuristics "
        "stay on regardless.</blockquote>\n"
        "<i>💡 Tap Cancel below to step back.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(ModFSM.del_word, F.text)
async def on_del(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ <b>Cancelled.</b>\n<i>The blocked-term list is unchanged.</i>")
        return
    await state.clear()
    removed = await remove_banned(raw)
    await log_action(message.chat.id, "mod_del", raw[:40])
    text, markup = await _panel()
    head = ("✅ <b>Term removed</b> — it's no longer blocked.\n\n" if removed
            else "ℹ️ <b>Not on the list</b> — nothing to remove.\n\n")
    await message.answer(head + text, reply_markup=markup)
