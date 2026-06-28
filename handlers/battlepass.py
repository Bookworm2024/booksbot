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
    head = [f"🎟️ <b>Battle Pass</b> · <i>{month} Season</i>",
            "━━━━━━━━━━━━━━━━━━",
            ("<i>Earn Pass Points as you read and play, then claim BGM at every tier.</i>"
             if st["premium"]
             else "<i>Earn Pass Points as you read and play — unlock Premium to double every reward.</i>"),
            "<blockquote>",
            ("👑 <b>Premium track:</b> unlocked — every premium reward is yours this season."
             if st["premium"]
             else f"🔓 <b>Premium track:</b> <code>{fmt_amount(PREMIUM_PRICE)}</code> BGM — unlocks the richer reward at every tier."),
            f"⭐ <b>Pass Points:</b> <code>{st['pp']}</code> / <code>{st['max_pp']}</code>",
            _bar(st["pp"], st["max_pp"]),
            "</blockquote>",
            "🏅 <b>Season tiers</b>"]
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
        head.append(f"{mark} <b>Tier {t['idx']+1}</b> · <code>{t['threshold']}</code> pp — "
                    f"🆓 <code>{fmt_amount(t['free'])}</code> BGM{prem}")
        if t["claimable"]:
            total = t["free"] + (t["premium"] if st["premium"] else 0)
            rows.append([btn(f"🎁 Claim Tier {t['idx']+1} (+{fmt_amount(total)} BGM)",
                             f"bp_claim:{t['idx']}", style="success")])
    head.append("<i>💡 Reach a tier's points to unlock it, then tap Claim — rewards land straight in your wallet.</i>")
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
        await call.answer(f"✨ Reward claimed — +{fmt_amount(paid)} BGM is in your wallet. Nicely done!", show_alert=True)
    else:
        await call.answer("This tier isn't ready yet — earn a few more Pass Points, or it's already been claimed.", show_alert=True)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "bp_buy")
async def cb_buy(call: CallbackQuery) -> None:
    res = await buy_premium(call.from_user.id)
    if res == "ok":
        await call.answer("👑 Premium unlocked — every premium reward this season is now yours. Enjoy!", show_alert=True)
    elif res == "already":
        await call.answer("You're already on the Premium track this season — every reward is unlocked.", show_alert=True)
    else:
        await call.answer(f"Premium costs {fmt_amount(PREMIUM_PRICE)} BGM. Top up your wallet and you'll be set.", show_alert=True)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)
