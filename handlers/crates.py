"""
handlers/crates.py — Loot Crates UI.

Account → 🎁 Loot Crates (also /crates). Earn keys by playing, downloading,
spinning and claiming; open crates for weighted BGM/BCN rewards.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.crates import open_crate, status, _TIERS, ACTIONS_PER_KEY
from utils.format import fmt_amount
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


def _bar(have: int, need: int, width: int = 10) -> str:
    filled = 0 if need <= 0 else max(0, min(width, have * width // need))
    return "🟦" * filled + "⬜" * (width - filled)


async def _view(uid: int):
    st = await status(uid)
    odds = " · ".join(f"{t[0]}" for t in _TIERS[2:])  # show the better tiers
    text = (
        "<b>🎁 Loot Crates</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <b>Keys:</b> {st['keys']}\n"
        f"📦 Next key: {_bar(st['progress'], st['need'])} {st['progress']}/{st['need']}\n"
        f"🏆 Opened: <b>{st['opened']}</b>\n\n"
        f"<i>Earn 1 key every {ACTIONS_PER_KEY} actions (play / download / spin / claim).\n"
        f"Possible drops include {odds}.</i>")
    rows = []
    if st["keys"] > 0:
        rows.append([btn(f"🔓 Open a Crate ({st['keys']} 🔑)", "crate_open", style="success")])
    else:
        rows.append([btn("🎮 Earn keys — Play", "menu_games", style="primary")])
    rows.append([btn("🔙 Account", "menu_account", style="danger")])
    return text, kb(*rows)


@router.callback_query(F.data == "menu_crates")
async def cb_crates(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.message(Command("crates"))
async def cmd_crates(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "crate_open")
async def cb_open(call: CallbackQuery) -> None:
    reward = await open_crate(call.from_user.id)
    if not reward:
        await call.answer("No keys yet — keep playing!", show_alert=True)
        text, markup = await _view(call.from_user.id)
        await call.message.edit_text(text, reply_markup=markup)
        return
    bits = [f"💎 +{fmt_amount(reward['bgm'])} BGM"] if reward["bgm"] else []
    if reward["bcn"]:
        bits.append(f"🪙 +{fmt_amount(reward['bcn'])} BCN")
    await call.answer("🎉 Crate opened!")
    st = await status(call.from_user.id)
    rows = []
    if st["keys"] > 0:
        rows.append([btn(f"🔓 Open Another ({st['keys']} 🔑)", "crate_open", style="success")])
    rows.append([btn("💼 Balance", "acc_balance", style="primary"),
                 btn("🔙 Account", "menu_account", style="danger")])
    await call.message.edit_text(
        "🎁 <b>Crate Opened!</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"{reward['tier']} drop:\n<b>{'  '.join(bits)}</b>\n\n"
        f"🔑 Keys left: <b>{st['keys']}</b>",
        reply_markup=kb(*rows))
