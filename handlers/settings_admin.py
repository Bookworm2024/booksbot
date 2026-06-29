"""
handlers/settings_admin.py — live pricing/economy editor (super-admin).

Admin panel → ⚙️ Pricing → see every money lever with its current value → tap
to edit → type a new number → it applies instantly, no redeploy. Backed by
utils.settings (Mongo kv).
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from utils.deals import banner, clear_deal, get_deal, set_deal
from utils.format import fmt_amount, valid_amount
from utils.keyboards import btn, cancel_row, kb
from utils.settings import DEFAULTS, all_settings, set_setting

logger = logging.getLogger(__name__)
router = Router()

_KEYS = list(DEFAULTS.keys())
_CATS = ["Pricing", "Rewards", "Economy", "Safety"]


class PriceFSM(StatesGroup):
    awaiting_value = State()
    deal_pct = State()
    deal_hours = State()


async def _panel():
    vals = await all_settings()
    lines = [
        "🛡 <b>Settings &amp; Economy</b>",
        "━━━━━━━━━━━━━━━━━━",
        "<i>Every money lever, live — edit a value and it applies instantly, no redeploy.</i>",
    ]
    rows = []
    for cat in _CATS:
        cat_keys = [k for k in _KEYS if DEFAULTS[k][3] == cat]
        if not cat_keys:
            continue
        body = [f"<blockquote><b>{cat}</b>"]
        for k in cat_keys:
            label = DEFAULTS[k][1]
            body.append(f"• {label}: <code>{fmt_amount(vals[k], 3)}</code>")
            rows.append([btn(f"✏️ {label}", f"price_edit:{k}", style="primary")])
        body.append("</blockquote>")
        lines.append("\n".join(body))
    lines.append("<i>💡 Tap any value below to set a new number — changes take effect on the next action.</i>")
    rows.append([btn("🔙 Back to Admin", "admin_open", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_pricing")
async def cb_pricing(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("This editor is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("price_edit:"))
async def cb_edit(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("This editor is reserved for the super admin.", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key not in DEFAULTS:
        await call.answer("We couldn't find that setting — please pick one from the panel.", show_alert=True)
        return
    await call.answer()
    await state.set_state(PriceFSM.awaiting_value)
    await state.update_data(key=key)
    await call.message.answer(
        f"✏️ <b>Edit {DEFAULTS[key][1]}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        "🔢 <b>Send a number</b> and it becomes the new value — it applies the moment you send it.\n"
        "💡 <i>Tap Cancel below to leave this setting untouched.</i>"
        "</blockquote>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(PriceFSM.awaiting_value, F.text)
async def on_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ <b>No changes made</b> — that setting is exactly as it was.")
        return
    data = await state.get_data()
    key = data.get("key")
    await state.clear()
    # valid_amount rejects inf/nan/negative/absurd — the old `value < 0` guard let
    # float("inf")/float("nan") through, which then exploded purchase-bonus math.
    ok, value = valid_amount(raw, allow_zero=True)
    if not ok:
        await message.answer(
            "⚠️ <b>That value won't work</b>\n"
            "<blockquote>"
            "Please send a plain, non-negative number — no <code>inf</code> and no shorthand like "
            "<code>1e21</code>.\n"
            "💡 <i>For example:</i> <code>5</code><i>,</i> <code>0.5</code> <i>or</i> <code>250</code>"
            "</blockquote>")
        return
    await set_setting(key, value)
    await message.answer(
        f"✨ <b>{DEFAULTS[key][1]} updated</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"📊 <b>New value:</b> <code>{fmt_amount(value, 3)}</code>\n"
        "⚡ <i>Live now — it takes effect on the very next action, no redeploy needed.</i>"
        "</blockquote>")


# ── flash sale / deals ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_deal")
async def cb_deal(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    cur = await banner() or "💤 <b>Status:</b> no flash sale running right now."
    await call.message.edit_text(
        "🔥 <b>Flash Sale</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>A timed bonus that makes every 💎 BGM purchase go further.</i>\n"
        f"<blockquote>{cur}\n"
        "🎁 <b>What buyers get:</b> extra bonus BGM on top of every purchase for the window you set\n"
        "💡 <i>Perfect for launches, paydays and quiet stretches you'd like to spark.</i>"
        "</blockquote>\n"
        "<i>Launch a fresh sale below, or wind the current one down.</i>",
        reply_markup=kb([btn("⚡ Launch Flash Sale", "deal_new", style="success")],
                        [btn("🛑 End Sale", "deal_clear", style="danger")],
                        [btn("🔙 Back to Admin", "admin_open", style="primary")]))


@router.callback_query(F.data == "deal_new")
async def cb_deal_new(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(PriceFSM.deal_pct)
    await call.message.answer(
        "⚡ <b>Launch a Flash Sale · Step 1 of 2</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        "🎁 <b>Bonus percent</b> — how much extra BGM buyers receive on each purchase.\n"
        "<i>For example, send</i> <code>50</code> <i>for a +50% bonus.</i>\n"
        "💡 <i>Tap Cancel below to stop without launching.</i>"
        "</blockquote>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(PriceFSM.deal_pct, F.text)
async def on_deal_pct(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Flash sale cancelled</b> — nothing was launched."); return
    if not raw.isdigit() or not (1 <= int(raw) <= 500):
        await message.answer(
            "⚠️ <b>Let's try that number again</b>\n"
            "<blockquote>The bonus needs to be a whole percent between <code>1</code> and <code>500</code> — "
            "for example <code>50</code>.</blockquote>")
        return
    await state.update_data(pct=int(raw))
    await state.set_state(PriceFSM.deal_hours)
    await message.answer(
        "⏱ <b>Launch a Flash Sale · Step 2 of 2</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        "⌛ <b>Duration</b> — how many hours the sale should run before it auto-ends.\n"
        "<i>For example, send</i> <code>6</code> <i>for a six-hour window.</i>"
        "</blockquote>")


@router.message(PriceFSM.deal_hours, F.text)
async def on_deal_hours(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Flash sale cancelled</b> — nothing was launched."); return
    try:
        hours = float(raw)
        if not (0 < hours <= 168):
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ <b>Let's try that duration again</b>\n"
            "<blockquote>Please enter the run-time in hours, anywhere from just above <code>0</code> "
            "up to <code>168</code> (a full week).</blockquote>")
        return
    data = await state.get_data()
    await state.clear()
    until = await set_deal(data["pct"], hours)
    await message.answer(
        "🔥 <b>Flash sale is live!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🎁 <b>Bonus:</b> <code>+{data['pct']}%</code> BGM on every purchase\n"
        f"⏳ <b>Runs until:</b> <code>{until.strftime('%d %b %H:%M UTC')}</code>\n"
        "💡 <i>Buyers see the boosted offer at checkout right away — it auto-ends on time.</i>"
        "</blockquote>")


@router.callback_query(F.data == "deal_clear")
async def cb_deal_clear(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await clear_deal()
    await call.answer("✅ Flash sale ended — purchases are back to standard bonuses.")
    await cb_deal(call)
