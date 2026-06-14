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
from utils.keyboards import btn, kb
from utils.settings import DEFAULTS, all_settings, set_setting

logger = logging.getLogger(__name__)
router = Router()

_KEYS = list(DEFAULTS.keys())


class PriceFSM(StatesGroup):
    awaiting_value = State()


async def _panel():
    vals = await all_settings()
    lines = ["<b>⚙️ Pricing &amp; Economy</b>\n━━━━━━━━━━━━━━━━━━"]
    rows = []
    for k in _KEYS:
        _, label, _kind = DEFAULTS[k]
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
