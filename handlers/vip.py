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
    lines = ["👑 <b>Premium Membership</b>",
             "<i>Read more, pay less — the best of the library, curated for you.</i>",
             "━━━━━━━━━━━━━━━━━━"]
    if st["active"]:
        cfg = TIERS[st["tier"]]
        lines.append(f"<blockquote>✅ <b>You're a member.</b>\n"
                     f"{cfg['emoji']} <b>{cfg['name']}</b> — active through "
                     f"<b>{st['until'].strftime('%d %b %Y')}</b>.\n"
                     f"<i>Every perk below is already working for you.</i></blockquote>")
    lines.append(f"💼 <b>Your wallet</b> — <code>{fmt_amount(bgm)} BGM</code> ready to spend.\n")
    lines.append("<i>Choose your tier — each one pays for itself the more you read.</i>\n")
    for t, cfg in TIERS.items():
        dl = "Free downloads — every book, no token cost" if cfg["dl_discount"] >= 1 else f"{int(cfg['dl_discount']*100)}% off every download"
        lines.append(
            f"{cfg['emoji']} <b>{cfg['name']}</b> · <code>{cfg['price']} BGM</code> / {cfg['days']} days\n"
            f"<blockquote>📚 {dl}\n"
            f"🪙 <b>{cfg['claim_mult']}×</b> on your daily claim — more free tokens, every day\n"
            f"🎁 <code>+{cfg['monthly_bgm']} BGM</code> credited instantly on joining\n"
            f"{cfg['emoji']} A {cfg['name']} badge on your profile</blockquote>")
    lines.append("<i>💡 Membership extends if you re-subscribe — your time never resets to zero.</i>")
    rows = [[btn(f"{cfg['emoji']} Join {cfg['name']} · {cfg['price']} BGM",
                 f"vip_buy:{t}", style="success")] for t, cfg in TIERS.items()]
    rows.append([btn("💎 Top up BGM", "acc_buy", style="primary"),
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
    await call.answer("👑 You're in — welcome to Premium! Enjoy every perk.")
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(f"{msg}\n\n" + text, reply_markup=markup)
