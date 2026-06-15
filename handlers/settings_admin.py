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
from utils.keyboards import btn, kb
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
    lines = ["<b>⚙️ Settings &amp; Economy</b>\n━━━━━━━━━━━━━━━━━━"]
    rows = []
    for cat in _CATS:
        cat_keys = [k for k in _KEYS if DEFAULTS[k][3] == cat]
        if not cat_keys:
            continue
        lines.append(f"\n<b>{cat}</b>")
        for k in cat_keys:
            label = DEFAULTS[k][1]
            lines.append(f"• {label}: <b>{vals[k]:g}</b>")
            rows.append([btn(f"✏️ {label}", f"price_edit:{k}", style="primary")])
    rows.append([btn("🔙 Back", "admin_open", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_pricing")
async def cb_pricing(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("price_edit:"))
async def cb_edit(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key not in DEFAULTS:
        await call.answer("Unknown setting", show_alert=True)
        return
    await call.answer()
    await state.set_state(PriceFSM.awaiting_value)
    await state.update_data(key=key)
    await call.message.answer(f"✏️ Send the new value for <b>{DEFAULTS[key][1]}</b> "
                              "(a number). /cancel to abort.")


@router.message(PriceFSM.awaiting_value, F.text)
async def on_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    data = await state.get_data()
    key = data.get("key")
    await state.clear()
    try:
        value = float(raw)
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Enter a non-negative number.")
        return
    await set_setting(key, value)
    await message.answer(f"✅ <b>{DEFAULTS[key][1]}</b> set to <b>{value:g}</b>. "
                         "Applies immediately.")


# ── flash sale / deals ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_deal")
async def cb_deal(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    cur = await banner() or "No active flash sale."
    await call.message.edit_text(
        f"<b>🔥 Flash Sale</b>\n━━━━━━━━━━━━━━━━━━\n{cur}\n\n"
        "Fire a timed bonus on all BGM purchases.",
        reply_markup=kb([btn("⚡ New Flash Sale", "deal_new", style="success")],
                        [btn("🛑 End Sale", "deal_clear", style="danger")],
                        [btn("🔙 Back", "admin_open", style="primary")]))


@router.callback_query(F.data == "deal_new")
async def cb_deal_new(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(PriceFSM.deal_pct)
    await call.message.answer("⚡ Bonus <b>percent</b> for the sale (e.g. 50): /cancel to abort.")


@router.message(PriceFSM.deal_pct, F.text)
async def on_deal_pct(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    if not raw.isdigit() or not (1 <= int(raw) <= 500):
        await message.answer("Enter a percent 1–500.")
        return
    await state.update_data(pct=int(raw))
    await state.set_state(PriceFSM.deal_hours)
    await message.answer("⏱ Duration in <b>hours</b> (e.g. 6):")


@router.message(PriceFSM.deal_hours, F.text)
async def on_deal_hours(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    try:
        hours = float(raw)
        if not (0 < hours <= 168):
            raise ValueError
    except ValueError:
        await message.answer("Enter hours 0–168.")
        return
    data = await state.get_data()
    await state.clear()
    until = await set_deal(data["pct"], hours)
    await message.answer(f"🔥 <b>Flash sale live!</b> +{data['pct']}% bonus BGM "
                         f"until {until.strftime('%d %b %H:%M UTC')}.")


@router.callback_query(F.data == "deal_clear")
async def cb_deal_clear(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Super admin only", show_alert=True)
        return
    await clear_deal()
    await call.answer("Sale ended")
    await cb_deal(call)
