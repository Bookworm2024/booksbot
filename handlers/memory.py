"""
handlers/memory.py — Memory Match (chat-based sequence memory).

🧠 Memory → memorize a sequence of book-emoji, tap ✅ Ready to hide it, then
reproduce it by tapping the palette in order. Get it right → BGM (more for longer
sequences) and the option to continue one tile longer. 6 plays/day. The sequence
lives in the FSM (never re-shown once hidden); the reward is credited once.
"""
import logging
import random
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.wallet import add_bgm

logger = logging.getLogger(__name__)
router = Router()

_DAILY = 6
_START_LEN = 3
_MAX_LEN = 8
_PALETTE = ["📕", "📗", "📘", "📙", "📔", "📓"]


def _now():
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _reward(length: int) -> float:
    return round(0.1 + 0.05 * (length - _START_LEN), 2)


def _palette_kb():
    rows, row = [], []
    for i, emo in enumerate(_PALETTE):
        row.append(btn(emo, f"mm:{i}", style="primary"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows


def _again_kb():
    return kb([btn("🧠 Play Again", "mm_new", style="success"),
               btn("🎮 Games", "menu_games", style="primary")])


class MemoryFSM(StatesGroup):
    showing = State()    # sequence visible, waiting for ✅ Ready
    repeating = State()  # sequence hidden, user tapping it back


async def _plays_today(db, uid: int) -> int:
    u = await db.find_one_global("users", {"user_id": uid},
                                 {"mm_day": 1, "mm_plays": 1}) or {}
    return int(u.get("mm_plays") or 0) if u.get("mm_day") == _today() else 0


async def _start(message: Message, uid: int, state: FSMContext, *, edit: bool, length: int) -> None:
    from utils.flags import is_on
    send = message.edit_text if edit else message.answer
    if not await is_on("games"):
        await send("🎮 <b>Games are paused</b> — check back soon!",
                   reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    prev = await _plays_today(db, uid)
    if prev >= _DAILY:
        await send(f"🧠 <b>Memory</b>\n\nDaily limit reached ({_DAILY}/day). Back tomorrow!",
                   reply_markup=kb([btn("🎮 Games", "menu_games", style="primary")]))
        return
    await db.safe_update("users", {"user_id": uid},
                         {"$set": {"mm_day": _today(), "mm_plays": prev + 1}})
    length = max(_START_LEN, min(_MAX_LEN, length))
    seq = [random.randrange(len(_PALETTE)) for _ in range(length)]
    await state.set_state(MemoryFSM.showing)
    await state.update_data(seq=seq, pos=0, length=length)
    shown = " ".join(_PALETTE[i] for i in seq)
    await send(f"🧠 <b>Memory Match</b> · level {length - _START_LEN + 1}\n"
               "━━━━━━━━━━━━━━━━━━\n"
               f"Memorize this sequence ({length} tiles):\n\n<b>{shown}</b>\n\n"
               "Tap <b>✅ Ready</b> when you've got it.",
               reply_markup=kb([btn("✅ Ready", "mm_ready", style="success")]))


@router.message(Command("memory"))
async def cmd_memory(message: Message, state: FSMContext) -> None:
    await _start(message, message.chat.id, state, edit=False, length=_START_LEN)


@router.callback_query(F.data == "menu_memory")
async def cb_open(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True, length=_START_LEN)


@router.callback_query(F.data == "mm_new")
async def cb_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start(call.message, call.from_user.id, state, edit=True, length=_START_LEN)


@router.callback_query(MemoryFSM.showing, F.data == "mm_ready")
async def cb_ready(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    length = int(data.get("length") or _START_LEN)
    await state.set_state(MemoryFSM.repeating)
    await call.message.edit_text(
        f"🧠 <b>Repeat the sequence!</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Tap the {length} tiles in order. Progress: 0/{length}",
        reply_markup=kb(*_palette_kb()))


@router.callback_query(MemoryFSM.repeating, F.data.startswith("mm:"))
async def cb_tap(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    seq = data.get("seq") or []
    pos = int(data.get("pos") or 0)
    length = int(data.get("length") or len(seq))
    try:
        pick = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    if pos >= len(seq):
        await call.answer(); return

    if pick != seq[pos]:
        await state.clear()
        await call.answer("❌ Wrong tile!")
        shown = " ".join(_PALETTE[i] for i in seq)
        await call.message.edit_text(
            f"❌ <b>Out of sync!</b>\nThe sequence was:\n\n<b>{shown}</b>",
            reply_markup=_again_kb())
        return

    pos += 1
    if pos >= len(seq):
        # solved → credit once (clear state first)
        await state.clear()
        rwd = _reward(length)
        await add_bgm(call.from_user.id, rwd)
        db = await MongoManager.get()
        await db.safe_update("users", {"user_id": call.from_user.id},
                             {"$inc": {"games_played": 1, "game_bgm": rwd}})
        from utils.missions import mark
        await mark(call.from_user.id, "play_game")
        await call.answer("✅ Perfect!")
        rows = [[btn("🎮 Games", "menu_games", style="primary")]]
        if length < _MAX_LEN:
            rows.insert(0, [btn(f"⬆️ Next (level {length - _START_LEN + 2})",
                                f"mm_next:{length + 1}", style="success")])
        else:
            rows.insert(0, [btn("🧠 Play Again", "mm_new", style="success")])
        await call.message.edit_text(
            f"🎉 <b>Correct — {length} tiles!</b>\n💎 <b>+{fmt_amount(rwd)} BGM</b>",
            reply_markup=kb(*rows))
        return

    await state.update_data(pos=pos)
    await call.answer("✅")
    await call.message.edit_text(
        f"🧠 <b>Keep going!</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Tap the {length} tiles in order. Progress: {pos}/{length}",
        reply_markup=kb(*_palette_kb()))


@router.callback_query(F.data.startswith("mm_next:"))
async def cb_next(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    try:
        length = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        length = _START_LEN
    await _start(call.message, call.from_user.id, state, edit=True, length=length)


# Stateless fallback: after a restart MemoryStorage is wiped, so a tap on an
# already-shown ✅ Ready / tile button has no FSM state. Scoped to mm_ready + mm:N
# only (not mm_new / mm_next: / menu_memory). Registered last → never shadows the
# in-flow handlers above.
@router.callback_query((F.data == "mm_ready") | F.data.startswith("mm:"))
async def cb_mm_expired(call: CallbackQuery) -> None:
    await call.answer("🧠 Round expired — start a new Memory game.", show_alert=True)
