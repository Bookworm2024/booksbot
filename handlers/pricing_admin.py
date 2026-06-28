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
        head = (
            "⚡ <b>Happy Hour</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Live now — every download costs less.</i>\n"
            "<blockquote>"
            f"🟢 <b>Discount live:</b> <code>{off}%</code> off every download\n"
            f"⏳ <b>Runs until:</b> <code>{until}</code>\n"
            "💡 <i>Readers see the lower price instantly at checkout.</i>"
            "</blockquote>\n"
            "<i>Swap to a different preset below, or wind it down anytime.</i>"
        )
    else:
        head = (
            "⚡ <b>Happy Hour</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>A timed treat — sitewide download discounts on tap.</i>\n"
            "<blockquote>"
            "💤 <b>Status:</b> no discount running right now\n"
            "🪄 <b>What it does:</b> shaves a flat percent off every download for a set window\n"
            "💡 <i>Great for a quick traffic and goodwill boost — pick a preset to launch.</i>"
            "</blockquote>\n"
            "<i>Choose a discount and duration below to go live in one tap.</i>"
        )
    rows = [
        [btn("⚡ 25% off · 2 hrs", "hh_set:25:2", style="success"),
         btn("⚡ 50% off · 2 hrs", "hh_set:50:2", style="success")],
        [btn("⚡ 50% off · 6 hrs", "hh_set:50:6", style="success"),
         btn("⚡ 50% off · 24 hrs", "hh_set:50:24", style="success")],
        [btn("⚡ 75% off · 3 hrs", "hh_set:75:3", style="success")],
        [btn("🛑 End Happy Hour", "hh_clear", style="danger")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return head, kb(*rows)


@router.callback_query(F.data == "admin_happy")
async def cb_happy(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _happy_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("hh_set:"))
async def cb_hh_set(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    try:
        _, pct, hours = call.data.split(":")
        until = await set_happy_hour(int(pct), float(hours))
    except (ValueError, IndexError):
        await call.answer("That preset didn't read cleanly — please tap one of the buttons again.", show_alert=True)
        return
    await log_action(call.from_user.id, "happy_hour", f"{pct}%off/{hours}h")
    await call.answer(f"⚡ Happy Hour is live — {pct}% off every download. Readers see it now.")
    text, markup = await _happy_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "hh_clear")
async def cb_hh_clear(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await clear_happy_hour()
    await log_action(call.from_user.id, "happy_hour", "clear")
    await call.answer("✅ Happy Hour wound down — downloads are back to standard pricing.")
    text, markup = await _happy_view()
    await call.message.edit_text(text, reply_markup=markup)


# ── Surge pricing ─────────────────────────────────────────────────────────────
async def _surge_view():
    on, max_pct = await _surge_settings()
    state = "🟢 Active" if on else "🔴 Paused"
    head = (
        f"📈 <b>Surge Pricing</b> — {state}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Demand-aware pricing — the most-loved titles earn a little more.</i>\n"
        "<blockquote>"
        f"📊 <b>Ceiling:</b> hot titles cost up to <code>+{max_pct:g}%</code>\n"
        "🔥 <b>How it scales</b> — by all-time downloads:\n"
        f"   • <code>200+</code> downloads → <code>+{max_pct:g}%</code>\n"
        f"   • <code>100+</code> downloads → <code>+{max_pct * 0.66:.0f}%</code>\n"
        f"   • <code>50+</code> downloads → <code>+{max_pct * 0.33:.0f}%</code>\n"
        "   • under <code>50</code> downloads → standard price, no surcharge\n"
        "💡 <i>Quiet titles stay affordable; only proven favourites tick up.</i>"
        "</blockquote>\n"
        "<i>Flip it on or off, and set the ceiling, below.</i>"
    )
    rows = [
        [btn(("🔴 Pause Surge" if on else "🟢 Activate Surge"), "surge_toggle",
             style="danger" if on else "success")],
        [btn("Ceiling +10%", "surge_max:10", style="primary"),
         btn("Ceiling +25%", "surge_max:25", style="primary"),
         btn("Ceiling +50%", "surge_max:50", style="primary")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return head, kb(*rows)


@router.callback_query(F.data == "admin_surge")
async def cb_surge(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _surge_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "surge_toggle")
async def cb_surge_toggle(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    db = await MongoManager.get()
    on, _ = await _surge_settings()
    await db.kv_set("surge_on", not on)
    await log_action(call.from_user.id, "surge", "off" if on else "on")
    await call.answer("🔴 Surge paused — every title is back to standard pricing." if on
                      else "🟢 Surge active — popular titles now flex with demand.")
    text, markup = await _surge_view()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("surge_max:"))
async def cb_surge_max(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("This control is reserved for the super admin.", show_alert=True)
        return
    try:
        pct = float(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer(); return
    db = await MongoManager.get()
    await db.kv_set("surge_max_pct", pct)
    await log_action(call.from_user.id, "surge_max", f"{pct:g}%")
    await call.answer(f"✅ Surge ceiling set to +{pct:g}% — the hottest titles top out here.")
    text, markup = await _surge_view()
    await call.message.edit_text(text, reply_markup=markup)
