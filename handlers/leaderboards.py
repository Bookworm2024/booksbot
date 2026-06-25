"""
handlers/leaderboards.py — unified competitive leaderboards (Social).

🏆 Leaderboards → Top Readers / Gamers / Referrers / Streaks, each top-10 with
medals plus the viewer's own rank.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

# key → (title, user-doc field, unit, is_float)
_BOARDS = {
    "dl":     ("📥 Top Readers", "downloads", "downloads", False),
    "game":   ("🎮 Top Gamers", "game_bgm", "BGM", True),
    "ref":    ("🎁 Top Referrers", "ref_count", "referrals", False),
    "streak": ("🔥 Top Streaks", "login_streak", "day streak", False),
}
_MEDALS = ["🥇", "🥈", "🥉"] + ["🏅"] * 7


def _hub_kb():
    rows = [[btn(t, f"lb:{k}", style="primary")] for k, (t, *_ ) in _BOARDS.items()]
    rows.append([btn("🔙 Back", "menu_tools", style="danger")])
    return kb(*rows)


@router.message(Command("leaderboards"))
async def cmd_lb(message: Message) -> None:
    await message.answer("<b>🏆 Leaderboards</b>\n━━━━━━━━━━━━━━━━━━\nPick a board:",
                         reply_markup=_hub_kb())


@router.callback_query(F.data == "lb_hub")
async def cb_hub(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text("<b>🏆 Leaderboards</b>\n━━━━━━━━━━━━━━━━━━\nPick a board:",
                                 reply_markup=_hub_kb())


@router.callback_query(F.data.startswith("lb:"))
async def cb_board(call: CallbackQuery) -> None:
    await call.answer()
    key = call.data.split(":", 1)[1]
    if key not in _BOARDS:
        return
    title, field, unit, is_float = _BOARDS[key]
    db = await MongoManager.get()
    top = await db.find_global("users", {field: {"$gt": 0}}, sort=[(field, -1)], limit=10,
                               proj={"user_id": 1, "first_name": 1, field: 1})
    if not top:
        body = "No entries yet — be the first!"
    else:
        rows = []
        for i, t in enumerate(top):
            v = t.get(field, 0)
            vs = f"{float(v):.2f}" if is_float else str(int(v))
            me = " ⬅️ <b>you</b>" if t.get("user_id") == call.from_user.id else ""
            rows.append(f"{_MEDALS[i]} {escape((t.get('first_name') or 'Player')[:18])} — "
                        f"<b>{vs}</b> {unit}{me}")
        body = "\n".join(rows)
    # the viewer's own rank
    mine = await db.find_one_global("users", {"user_id": call.from_user.id}, {field: 1})
    myval = (mine or {}).get(field, 0) or 0
    rank = await db.count_global("users", {field: {"$gt": myval}}) + 1 if myval else None
    rank_line = f"\n\n<i>Your rank: #{rank}</i>" if rank else "\n\n<i>You're not ranked yet.</i>"
    await call.message.edit_text(
        f"<b>🏆 {title}</b>\n━━━━━━━━━━━━━━━━━━\n{body}{rank_line}",
        reply_markup=kb([btn("🔙 Boards", "lb_hub", style="primary")]))
