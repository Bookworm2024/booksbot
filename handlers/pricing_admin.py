"""
handlers/pricing_admin.py — admin levers for dynamic download pricing.

Admin → 🧰 More Tools → ⚡ Happy Hour / 📈 Surge Pricing.
  Happy Hour — fire a timed % discount on downloads (presets) or clear it.
  Surge      — toggle the per-item popularity surcharge + cycle its max %.

Super-admin only (revenue lever). All state is in kv via utils.pricing, so
changes apply instantly with no redeploy.
"""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import SUPER_ADMIN_ID
from utils.audit import log_action
from utils.keyboards import btn, kb
from utils.pricing import (
    _surge_settings, clear_happy_hour, happy_hour, set_happy_hour,
)
from database.connection import MongoManager

logger = logging.getLogger(__name__)
router = Router()


def _super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


# ── Happy Hour ────────────────────────────────────────────────────────────────
async def _happy_view():
    hh = await happy_hour()
    if hh["active"]:
        off = int(round((1.0 - hh["factor"]) * 100))
        until = hh["until"].strftime("%d %b %H:%M UTC") if hh["until"] else "—"
        head = f"⚡ <b>Happy Hour ACTIVE</b>\nDownloads <b>{off}% off</b> · until {until}"
    else:
        head = "⚡ <b>Happy Hour</b>\n<i>No discount active.</i>"
    rows = [
        [btn("25% off · 2h", "hh_set:25:2", style="success"),
         btn("50% off · 2h", "hh_set:50:2", style="success")],
        [btn("50% off · 6h", "hh_set:50:6", style="success"),
         btn("50% off · 24h", "hh_set:50:24", style="success")],
        [btn("75% off · 3h", "hh_set:75:3", style="success")],
        [btn("🛑 End Happy Hour", "hh_clear", style="danger")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return head + "\n━━━━━━━━━━━━━━━━━━\nPick a preset:", kb(*rows)


@router.callback_query(F.data == "admin_happy")
async def cb_happy(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    text, markup = await _happy_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("hh_set:"))
async def cb_hh_set(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    try:
        _, pct, hours = call.data.split(":")
        until = await set_happy_hour(int(pct), float(hours))
    except (ValueError, IndexError):
        await call.answer("Bad preset", show_alert=True)
        return
    await log_action(call.from_user.id, "happy_hour", f"{pct}%off/{hours}h")
    await call.answer(f"⚡ Happy Hour on — {pct}% off")
    text, markup = await _happy_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "hh_clear")
async def cb_hh_clear(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await clear_happy_hour()
    await log_action(call.from_user.id, "happy_hour", "clear")
    await call.answer("Happy Hour ended")
    text, markup = await _happy_view()
    await call.message.edit_text(text, reply_markup=markup)


# ── Surge pricing ─────────────────────────────────────────────────────────────
async def _surge_view():
    on, max_pct = await _surge_settings()
    state = "🟢 ON" if on else "🔴 OFF"
    head = (f"📈 <b>Surge Pricing</b> — {state}\n━━━━━━━━━━━━━━━━━━\n"
            f"Hot titles cost more, up to <b>+{max_pct:g}%</b>.\n\n"
            "Tiers (by all-time downloads):\n"
            f"• 200+ → +{max_pct:g}%\n"
            f"• 100+ → +{max_pct * 0.66:.0f}%\n"
            f"• 50+ → +{max_pct * 0.33:.0f}%\n"
            "• under 50 → no surcharge")
    rows = [
        [btn(("🔴 Turn OFF" if on else "🟢 Turn ON"), "surge_toggle",
             style="danger" if on else "success")],
        [btn("Max +10%", "surge_max:10", style="primary"),
         btn("Max +25%", "surge_max:25", style="primary"),
         btn("Max +50%", "surge_max:50", style="primary")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return head, kb(*rows)


@router.callback_query(F.data == "admin_surge")
async def cb_surge(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    text, markup = await _surge_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "surge_toggle")
async def cb_surge_toggle(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    db = await MongoManager.get()
    on, _ = await _surge_settings()
    await db.kv_set("surge_on", not on)
    await log_action(call.from_user.id, "surge", "off" if on else "on")
    await call.answer(f"Surge {'OFF' if on else 'ON'}")
    text, markup = await _surge_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("surge_max:"))
async def cb_surge_max(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    try:
        pct = float(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    db = await MongoManager.get()
    await db.kv_set("surge_max_pct", pct)
    await log_action(call.from_user.id, "surge_max", f"{pct:g}%")
    await call.answer(f"Max surge +{pct:g}%")
    text, markup = await _surge_view()
    await call.message.edit_text(text, reply_markup=markup)
