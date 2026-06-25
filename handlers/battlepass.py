"""
handlers/battlepass.py — seasonal Battle Pass UI.

Games hub → 🎟️ Battle Pass (also /battlepass). Shows the season's tiers, your
Pass Points progress, claimable rewards, and the premium-pass upsell.
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.battlepass import (
    PREMIUM_PRICE, TIERS, buy_premium, claim as claim_tier, status,
)
from utils.format import fmt_amount
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


def _bar(pp: int, max_pp: int, width: int = 12) -> str:
    filled = 0 if max_pp <= 0 else max(0, min(width, pp * width // max_pp))
    return "🟪" * filled + "⬜" * (width - filled)


async def _view(uid: int):
    st = await status(uid)
    month = datetime.now(timezone.utc).strftime("%B %Y")
    head = [f"<b>🎟️ Battle Pass</b> · {month}",
            ("👑 <b>Premium unlocked</b>" if st["premium"]
             else f"🔓 Premium: <b>{fmt_amount(PREMIUM_PRICE)} BGM</b> (unlocks every premium reward)"),
            f"⭐ <b>{st['pp']}</b> / {st['max_pp']} Pass Points",
            _bar(st["pp"], st["max_pp"]),
            "━━━━━━━━━━━━━━━━━━"]
    rows = []
    for t in st["tiers"]:
        if t["claimed"]:
            mark = "🎁"
        elif t["claimable"]:
            mark = "✅"
        elif t["reached"]:
            mark = "✅"
        else:
            mark = "🔒"
        prem = f" · 👑 +{fmt_amount(t['premium'])}" if not st["premium"] else f" + 👑 {fmt_amount(t['premium'])}"
        head.append(f"{mark} <b>T{t['idx']+1}</b> ({t['threshold']} pp) — "
                    f"🆓 {fmt_amount(t['free'])} BGM{prem}")
        if t["claimable"]:
            total = t["free"] + (t["premium"] if st["premium"] else 0)
            rows.append([btn(f"🎁 Claim T{t['idx']+1} (+{fmt_amount(total)} BGM)",
                             f"bp_claim:{t['idx']}", style="success")])
    if not st["premium"]:
        rows.append([btn(f"👑 Unlock Premium ({fmt_amount(PREMIUM_PRICE)} BGM)",
                         "bp_buy", style="success")])
    rows.append([btn("🔄 Refresh", "menu_battlepass", style="primary"),
                 btn("🔙 Games", "menu_games", style="danger")])
    return "\n".join(head), kb(*rows)


@router.callback_query(F.data == "menu_battlepass")
async def cb_pass(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.message(Command("battlepass"))
async def cmd_pass(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("bp_claim:"))
async def cb_claim(call: CallbackQuery) -> None:
    try:
        idx = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    paid = await claim_tier(call.from_user.id, idx)
    if paid > 0:
        await call.answer(f"🎉 +{fmt_amount(paid)} BGM!", show_alert=True)
    else:
        await call.answer("Not claimable.", show_alert=True)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "bp_buy")
async def cb_buy(call: CallbackQuery) -> None:
    res = await buy_premium(call.from_user.id)
    if res == "ok":
        await call.answer("👑 Premium unlocked!", show_alert=True)
    elif res == "already":
        await call.answer("You already have premium this season.", show_alert=True)
    else:
        await call.answer(f"Need {fmt_amount(PREMIUM_PRICE)} BGM — top up first.", show_alert=True)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)
