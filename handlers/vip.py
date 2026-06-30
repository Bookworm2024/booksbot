"""
handlers/vip.py — 👑 Premium (the freemium tier).

One Premium tier, three ways to get it (all funnelled through utils.premium):
  • wallet ₹  (₹280 / 30 days)
  • wallet $  ($3 / 30 days)
  • BGM       (1000 BGM → 7 days)   ← the earn-your-way-in path

This screen is the single upsell target: every "🔒 Premium" lock and quota
ceiling across the bot routes here via callback "go_premium".
Perks themselves are enforced at each feature (quotas in utils.quota, Discover
gating, the bigger daily-claim multiplier in utils.vip).
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils import premium
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.vip import get_status
from utils.wallet import get_balances, get_money

logger = logging.getLogger(__name__)
router = Router()


def _perks_block() -> str:
    return (
        "<blockquote>"
        "📥 <b>Unlimited</b> archive downloads — no daily cap\n"
        "🙋 <b>3 eBook + 3 audiobook</b> admin requests / day (audiobooks are Premium-only)\n"
        "🤖 <b>5</b> AI searches / day + 🔎 Similar &amp; 🎭 By-Mood unlocked\n"
        "📝 <b>5</b> AI summaries / day\n"
        "🎮 <b>5</b> plays per game / day\n"
        "🔭 Full Discover — 🆕 New Arrivals · 🔗 Series Finder · 🎯 Challenges\n"
        "🎁 <b>2×</b> your daily BGM reward"
        "</blockquote>")


async def _view(uid: int):
    st = await get_status(uid)
    inr, usd = await get_money(uid)
    bgm, _ = await get_balances(uid)
    p_inr, p_usd = await premium.price_inr(), await premium.price_usd()
    days = await premium.money_days()
    b_cost, b_days = await premium.bgm_cost(), await premium.bgm_days()

    lines = ["👑 <b>Premium</b>",
             "<i>The whole library, wide open — no limits, no friction.</i>",
             "━━━━━━━━━━━━━━━━━━"]
    if st["active"]:
        lines.append("<blockquote>✅ <b>You're Premium.</b> Active through "
                     f"<b>{st['until'].strftime('%d %b %Y')}</b>. Every perk below is live — "
                     "renew any time to extend (your days never reset).</blockquote>")
    lines.append(_perks_block())
    lines.append(
        f"💼 <b>You hold:</b> <code>₹{fmt_amount(inr, 2)}</code> · <code>${fmt_amount(usd, 2)}</code> "
        f"wallet · <code>{fmt_amount(bgm)} BGM</code>\n")
    lines.append("<i>Choose how to unlock it:</i>")
    rows = [
        [btn(f"🇮🇳 Pay ₹{fmt_amount(p_inr)} / {days}d (wallet)", "prem_buy:inr", style="success")],
        [btn(f"💵 Pay ${fmt_amount(p_usd)} / {days}d (wallet)", "prem_buy:usd", style="success")],
        [btn(f"💎 Redeem {int(b_cost)} BGM → {b_days}d", "prem_bgm", style="primary")],
        [btn("💳 Top Up Wallet", "acc_buy", style="primary")],
        [btn("🔙 Back", "menu_account", style="danger")],
    ]
    return "\n".join(lines), kb(*rows)


@router.message(Command("premium"))
async def cmd_premium(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.in_({"acc_vip", "go_premium"}))
async def cb_premium(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    # go_premium can fire from screens that aren't editable into this view cleanly;
    # edit when possible, else send fresh.
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        await call.message.answer(text, reply_markup=markup)


# ── buy from the real-money wallet ────────────────────────────────────────────
@router.callback_query(F.data.startswith("prem_buy:"))
async def cb_prem_buy(call: CallbackQuery) -> None:
    cur = call.data.split(":", 1)[1]
    if cur not in ("inr", "usd"):
        await call.answer("That option isn't available — pick another.", show_alert=True)
        return
    ok, until = await premium.buy_with_wallet(call.from_user.id, cur)
    if not ok:
        price = await (premium.price_inr() if cur == "inr" else premium.price_usd())
        sym = "₹" if cur == "inr" else "$"
        await call.answer(
            f"Your {sym} wallet is short of {sym}{fmt_amount(price)}. Top up first — "
            "or redeem BGM instead.", show_alert=True)
        # nudge to top up
        from handlers.payments import _buy_view
        text, markup = await _buy_view(call.from_user.id)
        try:
            await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            pass
        return
    await call.answer("👑 Welcome to Premium — enjoy every perk!")
    await _premium_success(call, until)
    from utils.settings import get_float
    from utils.invoice import send_invoice
    days = int(await get_float("premium_days"))
    price = await (premium.price_inr() if cur == "inr" else premium.price_usd())
    await send_invoice(
        call.bot, call.from_user.id, item=f"Premium membership · {days} days",
        amount=price, currency=("INR" if cur == "inr" else "USD"),
        method="Wallet · " + ("INR (₹)" if cur == "inr" else "USD ($)"), prefix="PREM")


# ── redeem with BGM (the grind-to-premium path) ───────────────────────────────
@router.callback_query(F.data == "prem_bgm")
async def cb_prem_bgm(call: CallbackQuery) -> None:
    cost = int(await premium.bgm_cost())
    days = await premium.bgm_days()
    ok, until = await premium.redeem_with_bgm(call.from_user.id)
    if not ok:
        bgm, _ = await get_balances(call.from_user.id)
        await call.answer(
            f"You need {cost} BGM (you have {fmt_amount(bgm)}). Win more in games, "
            "referrals and daily rewards — or buy Premium from your wallet.",
            show_alert=True)
        return
    await call.answer(f"👑 {days} days of Premium unlocked with BGM — nice!")
    await _premium_success(call, until)
    from utils.invoice import send_invoice
    await send_invoice(
        call.bot, call.from_user.id, item=f"Premium membership · {int(days)} days",
        amount=cost, currency="BGM", method="BGM redemption", prefix="PREM")


async def _premium_success(call: CallbackQuery, until) -> None:
    text, markup = await _view(call.from_user.id)
    banner = ("👑 <b>You're Premium!</b>\n"
              f"<i>Active through <b>{until.strftime('%d %b %Y')}</b>. Every limit just lifted.</i>\n\n"
              if until else "")
    try:
        await call.message.edit_text(banner + text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        await call.message.answer(banner + text, reply_markup=markup)
