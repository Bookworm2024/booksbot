"""
handlers/missions.py — daily missions board.

🎯 Daily Missions → see today's tasks (✅/⬜) and claim the BGM you've earned.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.keyboards import btn, kb
from utils.missions import MISSIONS, claim, status

logger = logging.getLogger(__name__)
router = Router()


async def _board(uid: int):
    st = await status(uid)
    lines = ["<b>🎯 Daily Missions</b>", "━━━━━━━━━━━━━━━━━━"]
    for key, (label, reward) in MISSIONS.items():
        mark = "✅" if key in st["done"] else "⬜"
        tag = " (claimed)" if key in st["claimed"] else ""
        lines.append(f"{mark} {label} — <b>+{reward:g} BGM</b>{tag}")
    lines.append(f"\n💎 <b>Claimable now:</b> {st['claimable']:g} BGM")
    rows = []
    if st["claimable"] > 0:
        rows.append([btn("🎁 Claim Rewards", "missions_claim", style="success")])
    rows.append([btn("🎮 Play", "menu_games", style="primary"),
                 btn("🔙 Back", "menu_home", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.message(Command("missions"))
async def cmd_missions(message: Message) -> None:
    text, markup = await _board(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu_missions")
async def cb_missions(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _board(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "missions_claim")
async def cb_claim(call: CallbackQuery) -> None:
    got = await claim(call.from_user.id)
    await call.answer(f"+{got:g} BGM!" if got else "Nothing to claim yet.", show_alert=bool(got))
    text, markup = await _board(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)
