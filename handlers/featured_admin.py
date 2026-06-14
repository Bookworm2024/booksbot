"""
handlers/featured_admin.py — admin: manage sponsored / featured slots.

Admin panel → ⭐ Featured → ➕ Feature a Book → type a title → tap a result to
feature it for 7 days (sell the slot). Current featured list shown with remove
buttons.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from utils.featured import add_featured, featured_files, remove_featured
from utils.files import icon_for, search
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_FEATURE_DAYS = 7


class FeatFSM(StatesGroup):
    awaiting_query = State()


async def _panel():
    items = await featured_files(limit=10)
    rows = [[btn("➕ Feature a Book", "feat_add", style="success")]]
    lines = ["<b>⭐ Featured Slots</b>\n━━━━━━━━━━━━━━━━━━"]
    if items:
        for f in items:
            lines.append(f"⭐ {f.get('name','Untitled')[:40]}")
            rows.append([btn(f"🗑 {f.get('name','')[:28]}", f"feat_del:{f['file_unique_id']}",
                             style="danger")])
    else:
        lines.append("<i>No featured books.</i>")
    rows.append([btn("🔙 Back", "admin_open", style="primary")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_featured")
async def cb_featured_admin(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "feat_add")
async def cb_feat_add(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(FeatFSM.awaiting_query)
    await call.message.answer("🔍 Type a title to find the book to feature. /cancel to abort.")


@router.message(FeatFSM.awaiting_query, F.text)
async def on_feat_query(message: Message, state: FSMContext) -> None:
    q = (message.text or "").strip()
    if q.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    await state.clear()
    results, total = await search(q, limit=8)
    if not results:
        await message.answer("No matches. Try another title.")
        return
    rows = [[btn(f"⭐ {icon_for(f.get('ext',''))} {f.get('name','Untitled')[:34]}",
                 f"feat_set:{f['file_unique_id']}", style="success")] for f in results]
    await message.answer(f"Pick a book to feature for {_FEATURE_DAYS} days:",
                         reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("feat_set:"))
async def cb_feat_set(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    fuid = call.data.split(":", 1)[1]
    await add_featured(fuid, _FEATURE_DAYS)
    await call.answer("Featured ⭐")
    text, markup = await _panel()
    await call.message.answer(f"✅ Featured for {_FEATURE_DAYS} days.")
    await call.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("feat_del:"))
async def cb_feat_del(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await remove_featured(call.data.split(":", 1)[1])
    await call.answer("Removed")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)
