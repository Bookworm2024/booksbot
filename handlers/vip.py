"""
handlers/vip.py — Premium (VIP) subscriptions.

Account → 💎 Premium → see tiers + perks + your status → subscribe (spends BGM).
Perks (cheaper downloads, bigger claim, monthly BGM) are enforced in
request.py / economy.py via utils.vip.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.vip import TIERS, get_status, subscribe
from utils.wallet import get_balances

logger = logging.getLogger(__name__)
router = Router()


async def _view(uid: int):
    st = await get_status(uid)
    bgm, _ = await get_balances(uid)
    lines = ["<b>💎 Premium (VIP)</b>", "━━━━━━━━━━━━━━━━━━"]
    if st["active"]:
        cfg = TIERS[st["tier"]]
        lines.append(f"✅ Active: <b>{cfg['emoji']} {cfg['name']}</b> "
                     f"until {st['until'].strftime('%d %b %Y')}")
    lines.append(f"💎 Your balance: <b>{fmt_amount(bgm)} BGM</b>\n")
    for t, cfg in TIERS.items():
        dl = "free downloads" if cfg["dl_discount"] >= 1 else f"{int(cfg['dl_discount']*100)}% off downloads"
        lines.append(
            f"{cfg['emoji']} <b>{cfg['name']}</b> — {cfg['price']} BGM / {cfg['days']}d\n"
            f"   • {dl}\n   • {cfg['claim_mult']}× daily claim\n"
            f"   • +{cfg['monthly_bgm']} BGM now")
    rows = [[btn(f"{cfg['emoji']} Get {cfg['name']} ({cfg['price']} BGM)",
                 f"vip_buy:{t}", style="success")] for t, cfg in TIERS.items()]
    rows.append([btn("💎 Buy BGM", "acc_buy", style="primary"),
                 btn("🔙 Back", "menu_account", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.message(Command("vip"))
async def cmd_vip(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "acc_vip")
async def cb_vip(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("vip_buy:"))
async def cb_buy(call: CallbackQuery) -> None:
    tier = int(call.data.split(":", 1)[1])
    ok, msg = await subscribe(call.from_user.id, tier)
    if not ok:
        await call.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
        return
    await call.answer("Activated 🎉")
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(f"{msg}\n\n" + text, reply_markup=markup)
