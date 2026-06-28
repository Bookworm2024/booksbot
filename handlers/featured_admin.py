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

from utils.featured import add_featured, featured_files, remove_featured
from utils.files import icon_for, search
from utils.keyboards import btn, kb
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()

_FEATURE_DAYS = 7


class FeatFSM(StatesGroup):
    awaiting_query = State()


async def _panel():
    items = await featured_files(limit=10)
    rows = [[btn("➕ Feature a Book", "feat_add", style="success")]]
    lines = [
        "⭐ <b>Featured Slots</b>",
        "━━━━━━━━━━━━━━━━━━",
        "<i>Your spotlight shelf — the sponsored titles readers see first across Discover.</i>",
    ]
    if items:
        lines.append(f"\n<blockquote><b>Now in the spotlight ({len(items)})</b>")
        for f in items:
            lines.append(f"⭐ {f.get('name','Untitled')[:40]}")
        lines.append("Tap a 🗑 button below to clear a slot the moment a sponsorship ends.</blockquote>")
    else:
        lines.append(
            "\n<blockquote>Every slot is open right now.\n"
            "Tap <b>➕ Feature a Book</b> to promote a title — it stays in the spotlight "
            f"for <code>{_FEATURE_DAYS}</code> days.</blockquote>"
        )
    lines.append("\n<i>💡 Featured titles surface ahead of organic results — a premium slot worth selling.</i>")
    for f in items:
        rows.append([btn(f"🗑 {f.get('name','')[:28]}", f"feat_del:{f['file_unique_id']}",
                         style="danger")])
    rows.append([btn("🔙 Back to Admin", "admin_open", style="primary")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_featured")
async def cb_featured_admin(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "feat_add")
async def cb_feat_add(call: CallbackQuery, state: FSMContext) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(FeatFSM.awaiting_query)
    await call.message.answer(
        "🔭 <b>Find a Book to Feature</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Type a title and we'll pull matching entries from the archive. "
        "Pick one and it takes the spotlight straight away.</blockquote>\n"
        "<i>Send /cancel to step back.</i>"
    )


@router.message(FeatFSM.awaiting_query, F.text)
async def on_feat_query(message: Message, state: FSMContext) -> None:
    q = (message.text or "").strip()
    if q.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ <b>Cancelled.</b>\n<i>No slot was changed — nothing to undo.</i>")
        return
    await state.clear()
    results, total = await search(q, limit=8)
    if not results:
        await message.answer(
            "🔭 <b>No matches yet</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>We couldn't find that title in the archive. "
            "Try a shorter search or a different spelling.</blockquote>\n"
            "<i>💡 Tip: search the author's surname or one distinctive keyword.</i>"
        )
        return
    rows = [[btn(f"⭐ {icon_for(f.get('ext',''))} {f.get('name','Untitled')[:34]}",
                 f"feat_set:{f['file_unique_id']}", style="success")] for f in results]
    await message.answer(
        "⭐ <b>Choose Your Spotlight Title</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Tap any result below and it goes live in the featured shelf "
        f"for <code>{_FEATURE_DAYS}</code> days.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("feat_set:"))
async def cb_feat_set(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    fuid = call.data.split(":", 1)[1]
    await add_featured(fuid, _FEATURE_DAYS)
    await call.answer("Spotlight is live — featured for the next 7 days. ⭐")
    text, markup = await _panel()
    await call.message.answer(
        "✨ <b>Spotlight Live</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>This title now leads Discover for <code>{_FEATURE_DAYS}</code> days. "
        "We'll keep it featured until the slot expires — no further action needed.</blockquote>"
    )
    await call.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("feat_del:"))
async def cb_feat_del(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await remove_featured(call.data.split(":", 1)[1])
    await call.answer("Slot cleared — that title is no longer featured.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)
