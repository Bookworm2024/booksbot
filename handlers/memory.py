"""
handlers/memory.py — Memory Match (chat-based sequence memory).

🧠 Memory → memorize a sequence of book-emoji, tap ✅ Ready to hide it, then
reproduce it by tapping the palette in order. Get it right → BGM (more for longer
sequences) and the option to continue one tile longer. 6 plays/day. The sequence
lives in the FSM (never re-shown once hidden); the reward is credited once.
"""
import logging
import random
import uuid
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
               btn("🎮 More Games", "menu_games", style="primary")])


class MemoryFSM(StatesGroup):
    showing = State()    # sequence visible, waiting for ✅ Ready
    repeating = State()  # sequence hidden, user tapping it back


async def _consume_play(db, uid: int) -> bool:
    """Atomically count one play under the daily cap. Returns False when the cap is
    reached. The day-reset sets mm_plays=1 in the same op as the day flip, and the
    same-day path is a conditional $inc under the cap — so concurrent _start calls
    can never both slip past the limit (the BGM faucet for this game)."""
    today = _today()
    did_reset = await db.find_one_and_update_global(
        "users", {"user_id": uid, "mm_day": {"$ne": today}},
        {"$set": {"mm_day": today, "mm_plays": 1}})
    if did_reset is not None:
        return True  # first play of a new day
    inc = await db.find_one_and_update_global(
        "users", {"user_id": uid, "mm_day": today, "mm_plays": {"$lt": _DAILY}},
        {"$inc": {"mm_plays": 1}})
    return inc is not None


async def _start(message: Message, uid: int, state: FSMContext, *, edit: bool, length: int) -> None:
    from utils.flags import is_on
    send = message.edit_text if edit else message.answer
    if not await is_on("games"):
        await send("🎮 <b>Games are taking a short break</b>\n"
                   "━━━━━━━━━━━━━━━━━━━━\n"
                   "<blockquote>The arcade is paused for a little upkeep. Everything "
                   "you've earned is safe — pop back soon and your tables will be "
                   "right where you left them.</blockquote>",
                   reply_markup=kb([btn("🔙 Back", "menu_home", style="danger")]))
        return
    db = await MongoManager.get()
    if not await _consume_play(db, uid):
        await send(f"🧠 <b>Memory Match</b>\n"
                   "━━━━━━━━━━━━━━━━━━━━\n"
                   f"<i>That's a full round of training for today.</i>\n\n"
                   f"<blockquote>You've played all <code>{_DAILY}</code> of today's "
                   "rounds — nicely done. Your plays refresh at midnight, so come back "
                   "tomorrow for a fresh set and another shot at the 💎 BGM rewards. "
                   "Plenty of other games are open in the meantime.</blockquote>",
                   reply_markup=kb([btn("🎮 More Games", "menu_games", style="primary")]))
        return
    length = max(_START_LEN, min(_MAX_LEN, length))
    seq = [random.randrange(len(_PALETTE)) for _ in range(length)]
    rt = uuid.uuid4().hex  # per-round token → atomic single-winner reward claim
    await state.set_state(MemoryFSM.showing)
    await state.update_data(seq=seq, pos=0, length=length, mm_round=rt)
    shown = " ".join(_PALETTE[i] for i in seq)
    await send(f"🧠 <b>Memory Match</b>  ·  <i>level {length - _START_LEN + 1}</i>\n"
               "━━━━━━━━━━━━━━━━━━━━\n"
               "<i>Lock the order in, then play it back from memory.</i>\n\n"
               f"<blockquote>Study these <code>{length}</code> tiles:\n\n"
               f"<b>{shown}</b>\n\n"
               "Take your time — when you've got the order, tap <b>✅ Ready</b> and "
               "we'll hide them. Replay it perfectly to earn 💎 BGM, and the further "
               "you climb, the bigger the reward.</blockquote>",
               reply_markup=kb([btn("✅ I'm Ready", "mm_ready", style="success")]))


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
        f"🧠 <b>Now play it back</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "<i>The tiles are hidden — recreate the order from memory.</i>\n\n"
        f"<blockquote>Tap the palette below in the exact sequence you memorised, all "
        f"<code>{length}</code> in a row.\n\n"
        f"Progress: <code>0/{length}</code></blockquote>",
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
        await call.answer("So close — that tile broke the chain. Give it another go!")
        shown = " ".join(_PALETTE[i] for i in seq)
        await call.message.edit_text(
            "❌ <b>That broke the sequence</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            "<i>One tile out of place — it happens to the best of us.</i>\n\n"
            f"<blockquote>Here's how the full sequence ran:\n\n<b>{shown}</b>\n\n"
            "Memory is a muscle — line it up next time and the 💎 BGM is yours. "
            "Ready for another round?</blockquote>",
            reply_markup=_again_kb())
        return

    pos += 1
    if pos >= len(seq):
        # solved → credit exactly once. state.clear() is NOT a dedup guard (FSM
        # isolation is disabled, so a fast double-tap on the last tile runs two
        # concurrent tasks). Gate the reward on an atomic per-round-token claim:
        # only the task that flips mm_solved_token to this round's token pays out.
        await state.clear()
        db = await MongoManager.get()
        rt = data.get("mm_round") or uuid.uuid4().hex
        won = await db.find_one_and_update_global(
            "users", {"user_id": call.from_user.id, "mm_solved_token": {"$ne": rt}},
            {"$set": {"mm_solved_token": rt}})
        rwd = _reward(length)
        if won is not None:
            await add_bgm(call.from_user.id, rwd)
            await db.safe_update("users", {"user_id": call.from_user.id},
                                 {"$inc": {"games_played": 1, "game_bgm": rwd}})
            from utils.missions import mark
            await mark(call.from_user.id, "play_game")
        await call.answer("✨ Flawless recall — reward credited!")
        rows = [[btn("🎮 More Games", "menu_games", style="primary")]]
        if length < _MAX_LEN:
            rows.insert(0, [btn(f"⬆️ Next Level ({length - _START_LEN + 2})",
                                f"mm_next:{length + 1}", style="success")])
        else:
            rows.insert(0, [btn("🧠 Play Again", "mm_new", style="success")])
        nxt = ("<i>Step up a tile for a bigger payout — your streak is just getting "
               "started.</i>" if length < _MAX_LEN
               else "<i>That's the top tier — a perfect memory. Run it again for more "
               "💎 BGM.</i>")
        await call.message.edit_text(
            f"✨ <b>Flawless — all {length} tiles!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>Credited to your wallet: 💎 <b>+{fmt_amount(rwd)} BGM</b>\n\n"
            f"{nxt}</blockquote>",
            reply_markup=kb(*rows))
        return

    await state.update_data(pos=pos)
    await call.answer("✅ Spot on — keep the chain going!")
    await call.message.edit_text(
        f"🧠 <b>Keep going — you're on track</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Stay with the order you memorised.</i>\n\n"
        f"<blockquote>Tap the next tile in the sequence — all <code>{length}</code> "
        f"to claim the reward.\n\n"
        f"Progress: <code>{pos}/{length}</code></blockquote>",
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
    await call.answer("🧠 This round has wrapped up — start a fresh Memory Match to play on.", show_alert=True)
