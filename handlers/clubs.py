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
from utils.keyboards import btn, cancel_row, kb

logger = logging.getLogger(__name__)
router = Router()


class ClubFSM(StatesGroup):
    name = State()
    desc = State()
    post = State()


async def _hub(uid: int):
    clubs = await list_clubs(20)
    mine = await my_club_ids(uid)
    lines = ["📖 <b>Book Clubs</b>",
             "━━━━━━━━━━━━━━━━━━━━",
             "<i>Reading rooms where fellow readers gather to swap notes, picks and pages.</i>",
             "",
             "<blockquote>Step inside a room to read the latest chatter, then join to add "
             "your own voice. Found your people? Start a club of your own and set the "
             "shelf.</blockquote>"]
    rows = []
    if not clubs:
        lines.append("\n<i>📚 No rooms are open just yet — be the very first to start one, and "
                     "the conversation begins with you.</i>")
    for c in clubs[:12]:
        tag = "✅ " if c["club_id"] in mine else ""
        rows.append([btn(f"{tag}{c.get('name','Club')[:28]} · 👥{int(c.get('member_count') or 0)}",
                         f"club_view:{c['club_id']}", style="primary")])
    rows.append([btn("➕ Start a Club", "club_create", style="success")])
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
        return ("🔒 <b>Room closed</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>This club has been wound down and is no longer open. Plenty more "
                "reading rooms are waiting for you back in the lounge.</i>"), \
               kb([btn("🔙 Browse Clubs", "menu_clubs", style="danger")])
    member = await is_member(club_id, uid)
    posts = await recent_posts(club_id, 6)
    lines = [f"📖 <b>{escape(club.get('name','Club'))}</b>",
             f"<i>{escape(club.get('desc','') or 'A cosy reading room.')}</i>",
             f"👥 <code>{int(club.get('member_count') or 0)}</code> member(s) reading together",
             "━━━━━━━━━━━━━━━━━━━━"]
    if posts:
        lines.append("<b>🔔 Latest from the room</b>")
        feed = []
        for p in reversed(posts):
            who = escape(p.get("name") or "Reader")
            feed.append(f"💬 <b>{who}</b>: {escape(p.get('text',''))}")
        lines.append("<blockquote>" + "\n".join(feed) + "</blockquote>")
    else:
        lines.append("<i>📚 The room is quiet for now — break the ice and leave the first note. "
                     "A simple hello is all it takes to get the pages turning.</i>")
    rows = []
    if member:
        rows.append([btn("✍️ Share a Post", f"club_post:{club_id}", style="success"),
                     btn("🚪 Leave Room", f"club_leave:{club_id}", style="danger")])
    else:
        rows.append([btn("✅ Join the Room", f"club_join:{club_id}", style="success")])
    rows.append([btn("🔄 Refresh Feed", f"club_view:{club_id}", style="primary"),
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
    await call.answer("✅ You're in! Welcome to the room — say hello whenever you're ready.")
    text, markup = await _club_view(call.from_user.id, club_id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("club_leave:"))
async def cb_leave(call: CallbackQuery) -> None:
    club_id = call.data.split(":", 1)[1]
    await leave(club_id, call.from_user.id)
    await call.answer("👋 You've left the room. The door stays open — rejoin any time.")
    text, markup = await _club_view(call.from_user.id, club_id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("club_post:"))
async def cb_post(call: CallbackQuery, state: FSMContext) -> None:
    club_id = call.data.split(":", 1)[1]
    if not await is_member(club_id, call.from_user.id):
        await call.answer("🔒 Join the room first, then your words will reach everyone inside.",
                          show_alert=True)
        return
    await call.answer()
    await state.set_state(ClubFSM.post)
    await state.update_data(club_id=club_id)
    await call.message.answer(
        "✍️ <b>Share a Post</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>The room is listening — type your message and we'll post it to the feed.</i>\n\n"
        f"<blockquote>📚 Drop a recommendation, a hot take, or a question for fellow readers.\n"
        f"✏️ Up to <code>{MAX_POST_LEN}</code> characters.\n"
        "↩️ Tap Cancel below any time to step back.</blockquote>",
        reply_markup=kb(cancel_row("menu_clubs")))


@router.message(ClubFSM.post, F.text)
async def on_post(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Post cancelled.</b> <i>Nothing was shared — pop back whenever inspiration strikes.</i>"); return
    data = await state.get_data()
    await state.clear()
    club_id = data.get("club_id")
    if not club_id or not raw:
        await message.answer("📭 <b>Nothing to share</b>\n<i>Your message came through empty, so there was nothing to post. Type a few words and try again.</i>"); return
    from utils.moderation import check
    ok, reason = await check(raw)
    if not ok:
        await message.answer("⚠️ <b>Post held back</b>\n"
                             "━━━━━━━━━━━━━━━━━━━━\n"
                             f"<i>Our house rules flagged this one ({escape(reason)}). Reword it "
                             "to keep the room welcoming, then send it again.</i>",
                             reply_markup=kb([btn("📖 Back to the Room", f"club_view:{club_id}", style="primary")]))
        return
    await add_post(club_id, message.chat.id,
                   message.from_user.first_name or "Reader", raw[:MAX_POST_LEN])
    text, markup = await _club_view(message.chat.id, club_id)
    await message.answer("✨ <b>Posted to the room.</b> <i>Your message is live on the feed below.</i>\n\n" + text, reply_markup=markup,
                         disable_web_page_preview=True)


# ── create a club ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "club_create")
async def cb_create(call: CallbackQuery, state: FSMContext) -> None:
    if await created_count(call.from_user.id) >= MAX_CLUBS_PER_USER:
        await call.answer(f"👑 You've reached your limit of {MAX_CLUBS_PER_USER} clubs. Wind one "
                          "down to make room for a new one.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ClubFSM.name)
    await call.message.answer(
        "➕ <b>Start a Club</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Every great reading room begins with a name. What shall we call yours?</i>\n\n"
        "<blockquote>📖 Pick something that tells readers what you're here to read — a genre, "
        "an author, a vibe.\n"
        "↩️ Tap Cancel below to step back.</blockquote>",
        reply_markup=kb(cancel_row("menu_clubs")))


@router.message(ClubFSM.name, F.text)
async def on_name(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Setup cancelled.</b> <i>No club was created — start again whenever you're ready.</i>"); return
    if len(raw) < 3:
        await message.answer("⚠️ <b>A touch too short</b>\n<i>Club names need at least <code>3</code> characters so readers can find you. Give it another go.</i>"); return
    await state.update_data(club_name=raw[:60])
    await state.set_state(ClubFSM.desc)
    await message.answer(
        "📝 <b>Add a Description</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>One or two lines on what your room is about — it's the first thing newcomers read.</i>\n\n"
        "<blockquote>✨ Set the tone, name a current read, or simply say who it's for.\n"
        "⏭ Send <code>skip</code> to leave it blank for now.</blockquote>")


@router.message(ClubFSM.desc, F.text)
async def on_desc(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Setup cancelled.</b> <i>No club was created — start again whenever you're ready.</i>"); return
    desc = "" if raw.lower() == "skip" else raw[:300]
    data = await state.get_data()
    await state.clear()
    if await created_count(message.chat.id) >= MAX_CLUBS_PER_USER:
        await message.answer(f"👑 <b>Club limit reached</b>\n<i>You can run up to <code>{MAX_CLUBS_PER_USER}</code> clubs at once. Wind one down to make room for another.</i>"); return
    club_id = await create_club(data.get("club_name", "Club"), desc, message.chat.id)
    text, markup = await _club_view(message.chat.id, club_id)
    await message.answer("🎉 <b>Your club is live!</b> <i>You're the founder and first member — invite a few readers and get the conversation started.</i>\n\n" + text, reply_markup=markup,
                         disable_web_page_preview=True)
