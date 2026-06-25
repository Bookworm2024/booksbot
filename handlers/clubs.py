"""
handlers/clubs.py — Book Clubs / Reading Rooms (Social).

Library → 👥 Book Clubs (also /clubs): browse & join clubs, read the room's
recent posts, and post your own. Create your own club (up to a few each).
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from utils.clubs import (
    MAX_CLUBS_PER_USER, MAX_POST_LEN, add_post, create_club, created_count,
    get_club, is_member, join, leave, list_clubs, my_club_ids, recent_posts,
)
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


class ClubFSM(StatesGroup):
    name = State()
    desc = State()
    post = State()


async def _hub(uid: int):
    clubs = await list_clubs(20)
    mine = await my_club_ids(uid)
    lines = ["<b>👥 Book Clubs</b>", "━━━━━━━━━━━━━━━━━━",
             "Join a reading room to chat about books."]
    rows = []
    if not clubs:
        lines.append("\n<i>No clubs yet — be the first to start one!</i>")
    for c in clubs[:12]:
        tag = "✅ " if c["club_id"] in mine else ""
        rows.append([btn(f"{tag}{c.get('name','Club')[:28]} · 👥{int(c.get('member_count') or 0)}",
                         f"club_view:{c['club_id']}", style="primary")])
    rows.append([btn("➕ Create Club", "club_create", style="success")])
    rows.append([btn("🔙 Library", "menu_library", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "menu_clubs")
async def cb_clubs(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    text, markup = await _hub(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.message(Command("clubs"))
async def cmd_clubs(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _hub(message.chat.id)
    await message.answer(text, reply_markup=markup)


async def _club_view(uid: int, club_id: str):
    club = await get_club(club_id)
    if not club:
        return "This club no longer exists.", kb([btn("🔙 Clubs", "menu_clubs", style="danger")])
    member = await is_member(club_id, uid)
    posts = await recent_posts(club_id, 6)
    lines = [f"<b>👥 {escape(club.get('name','Club'))}</b>",
             f"<i>{escape(club.get('desc','') or 'A reading room.')}</i>",
             f"👤 {int(club.get('member_count') or 0)} member(s)",
             "━━━━━━━━━━━━━━━━━━"]
    if posts:
        for p in reversed(posts):
            who = escape(p.get("name") or "Reader")
            lines.append(f"💬 <b>{who}</b>: {escape(p.get('text',''))}")
    else:
        lines.append("<i>No messages yet — say hello!</i>")
    rows = []
    if member:
        rows.append([btn("✍️ Post a Message", f"club_post:{club_id}", style="success"),
                     btn("🚪 Leave", f"club_leave:{club_id}", style="danger")])
    else:
        rows.append([btn("✅ Join Club", f"club_join:{club_id}", style="success")])
    rows.append([btn("🔄 Refresh", f"club_view:{club_id}", style="primary"),
                 btn("🔙 Clubs", "menu_clubs", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data.startswith("club_view:"))
async def cb_view(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _club_view(call.from_user.id, call.data.split(":", 1)[1])
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("club_join:"))
async def cb_join(call: CallbackQuery) -> None:
    club_id = call.data.split(":", 1)[1]
    await join(club_id, call.from_user.id)
    await call.answer("✅ Joined!")
    text, markup = await _club_view(call.from_user.id, club_id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("club_leave:"))
async def cb_leave(call: CallbackQuery) -> None:
    club_id = call.data.split(":", 1)[1]
    await leave(club_id, call.from_user.id)
    await call.answer("Left the club.")
    text, markup = await _club_view(call.from_user.id, club_id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("club_post:"))
async def cb_post(call: CallbackQuery, state: FSMContext) -> None:
    club_id = call.data.split(":", 1)[1]
    if not await is_member(club_id, call.from_user.id):
        await call.answer("Join the club first.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ClubFSM.post)
    await state.update_data(club_id=club_id)
    await call.message.answer(
        f"✍️ <b>Post to the club</b> (max {MAX_POST_LEN} chars). Send your message, "
        "or /cancel.")


@router.message(ClubFSM.post, F.text)
async def on_post(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    data = await state.get_data()
    await state.clear()
    club_id = data.get("club_id")
    if not club_id or not raw:
        await message.answer("Nothing posted."); return
    from utils.moderation import check
    ok, reason = await check(raw)
    if not ok:
        await message.answer(f"⚠️ <b>Message blocked</b> ({reason}). Please revise and try again.",
                             reply_markup=kb([btn("👥 Back to Club", f"club_view:{club_id}", style="primary")]))
        return
    await add_post(club_id, message.chat.id,
                   message.from_user.first_name or "Reader", raw[:MAX_POST_LEN])
    text, markup = await _club_view(message.chat.id, club_id)
    await message.answer("✅ Posted!\n\n" + text, reply_markup=markup,
                         disable_web_page_preview=True)


# ── create a club ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "club_create")
async def cb_create(call: CallbackQuery, state: FSMContext) -> None:
    if await created_count(call.from_user.id) >= MAX_CLUBS_PER_USER:
        await call.answer(f"You can create up to {MAX_CLUBS_PER_USER} clubs.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ClubFSM.name)
    await call.message.answer("➕ <b>Create a Club</b>\n\nSend the club <b>name</b>. /cancel to abort.")


@router.message(ClubFSM.name, F.text)
async def on_name(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    if len(raw) < 3:
        await message.answer("⚠️ Name must be at least 3 characters."); return
    await state.update_data(club_name=raw[:60])
    await state.set_state(ClubFSM.desc)
    await message.answer("📝 Send a short <b>description</b> (or <code>skip</code>).")


@router.message(ClubFSM.desc, F.text)
async def on_desc(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    desc = "" if raw.lower() == "skip" else raw[:300]
    data = await state.get_data()
    await state.clear()
    if await created_count(message.chat.id) >= MAX_CLUBS_PER_USER:
        await message.answer("You've reached the club creation limit."); return
    club_id = await create_club(data.get("club_name", "Club"), desc, message.chat.id)
    text, markup = await _club_view(message.chat.id, club_id)
    await message.answer("✅ <b>Club created!</b>\n\n" + text, reply_markup=markup,
                         disable_web_page_preview=True)
