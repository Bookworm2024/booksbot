"""
handlers/ads.py — sponsored ad slots: user view + admin management.

User: taps a 📢 ad button on the dashboard → sees the ad + an Open link (click
tracked). Admin (super) → 🧰 More Tools → 📢 Ad Slots → create / toggle / delete
with live impression & click stats.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from utils.ads import (
    all_ads, bump_click, create_ad, delete_ad, get_ad, set_active,
)
from utils.audit import log_action
from utils.keyboards import btn, cancel_row, kb, url_btn

logger = logging.getLogger(__name__)
router = Router()


def _super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


# ── user-facing ad view ───────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("ad:"))
async def cb_ad(call: CallbackQuery) -> None:
    ad_id = call.data.split(":", 1)[1]
    ad = await get_ad(ad_id)
    if not ad or not ad.get("active"):
        await call.answer("This offer has wrapped up — but there's plenty more waiting back in the menu.", show_alert=True)
        return
    await call.answer()
    await bump_click(ad_id)
    rows = []
    if ad.get("url"):
        rows.append([url_btn("🔗 Open offer", ad["url"], style="success")])
    rows.append([btn("🔙 Back to menu", "menu_home", style="danger")])
    await call.message.edit_text(
        "📢 <b>Sponsored</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>A handpicked offer from a partner we think you'll like.</i>\n\n"
        f"<blockquote>{escape(ad.get('text',''))}</blockquote>",
        reply_markup=kb(*rows), disable_web_page_preview=False)


# ── admin: ad slots ───────────────────────────────────────────────────────────
class AdFSM(StatesGroup):
    text = State()
    url = State()
    label = State()
    weight = State()


async def _panel():
    ads = await all_ads()
    lines = [
        "📢 <b>Sponsored Ad Slots</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<i>Promo placements surfaced on the dashboard — weighted, with live impression and click stats.</i>\n",
    ]
    if not ads:
        lines.append(
            "<blockquote>No ad slots yet. Tap <b>New ad</b> below to write your first placement — "
            "it goes live on the dashboard the moment it's saved.</blockquote>")
    rows = []
    for a in ads:
        on = a.get("active")
        ctr = (100.0 * int(a.get("clicks") or 0) / int(a.get("impressions") or 1))
        lines.append(
            f"{'🟢' if on else '🔴'} <b>{escape(a.get('label','📢'))}</b> "
            f"· <i>weight</i> <code>{a.get('weight',1)}</code>\n"
            f"   👁 <code>{int(a.get('impressions') or 0)}</code> seen · "
            f"🖱 <code>{int(a.get('clicks') or 0)}</code> clicks · "
            f"📊 <code>{ctr:.0f}%</code> CTR")
        rows.append([btn(f"{'⏸ Pause' if on else '▶️ Resume'} {a.get('label','')[:16]}",
                         f"ad_tog:{a['ad_id']}", style="danger" if on else "success"),
                     btn("🗑 Delete", f"ad_del:{a['ad_id']}", style="danger")])
    if ads:
        lines.append("\n<i>🟢 live · 🔴 paused · higher weight = shown more often.</i>")
    rows.append([btn("➕ New ad", "ad_new", style="success")])
    rows.append([btn("🔙 More Tools", "admin_more", style="primary")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_ads")
async def cb_ads(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("👑 Super admin only — ad slots are managed by the owner.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "ad_new")
async def cb_ad_new(call: CallbackQuery, state: FSMContext) -> None:
    if not _super(call.from_user.id):
        await call.answer("👑 Super admin only — ad slots are managed by the owner.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdFSM.text)
    await call.message.answer(
        "📢 <b>New Ad · Step 1 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Let's build a sponsored placement. We'll take it one step at a time.</i>\n\n"
        "<blockquote>✍️ Send the <b>message text</b> readers will see when they open this ad.\n"
        "Keep it inviting and to the point — this is the body of the offer.</blockquote>\n"
        "<i>💡 Tap Cancel below to step back.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(AdFSM.text, F.text)
async def on_ad_text(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled</b> — no ad was created. Nothing changed."); return
    await state.update_data(ad_text=raw)
    await state.set_state(AdFSM.url)
    await message.answer(
        "🔗 <b>New Ad · Step 2 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send a <b>link URL</b> for the offer — readers tap through to it from the ad.\n"
        "No destination? Send <code>skip</code> to leave it as a message-only placement.</blockquote>\n"
        "<i>💡 http, https or tg links are accepted.</i>")


@router.message(AdFSM.url, F.text)
async def on_ad_url(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled</b> — no ad was created. Nothing changed."); return
    url = "" if raw.lower() == "skip" else raw
    if url and not url.lower().startswith(("http://", "https://", "tg://", "https://t.me")):
        await message.answer(
            "⚠️ <b>That link doesn't look right</b>\n"
            "<i>Please send a full <code>http://</code>, <code>https://</code> or <code>tg://</code> link — "
            "or <code>skip</code> for a message-only ad.</i>")
        return
    await state.update_data(ad_url=url)
    await state.set_state(AdFSM.label)
    await message.answer(
        "🏷 <b>New Ad · Step 3 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send a short <b>button label</b> — the tappable text readers see on the dashboard.\n"
        "Make it pop, for example <code>🔥 50% Off Today</code>.</blockquote>\n"
        "<i>💡 Keep it to a few words so it fits the button neatly.</i>")


@router.message(AdFSM.label, F.text)
async def on_ad_label(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled</b> — no ad was created. Nothing changed."); return
    await state.update_data(ad_label=raw[:40])
    await state.set_state(AdFSM.weight)
    await message.answer(
        "⚖️ <b>New Ad · Step 4 of 4</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send a <b>weight from 1 to 10</b> to set how often this ad rotates in.\n"
        "Higher weight = shown more often. Use it to give your best-paying sponsors more airtime.</blockquote>\n"
        "<i>💡 Not sure? <code>1</code> is a fair, even share.</i>")


@router.message(AdFSM.weight, F.text)
async def on_ad_weight(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled</b> — no ad was created. Nothing changed."); return
    if not raw.isdigit() or not (1 <= int(raw) <= 10):
        await message.answer(
            "⚠️ <b>That weight isn't valid</b>\n"
            "<i>Send a whole number from <code>1</code> to <code>10</code> — nothing else.</i>")
        return
    data = await state.get_data()
    await state.clear()
    ad_id = await create_ad(data.get("ad_text", ""), data.get("ad_url", ""),
                            data.get("ad_label", "📢 Sponsored"), int(raw), message.chat.id)
    await log_action(message.chat.id, "ad_create", ad_id)
    await message.answer(
        "✨ <b>Ad is live</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Your sponsored slot is now rotating on the dashboard.</i>\n\n"
        f"<blockquote>🆔 Slot ID · <code>{ad_id}</code>\n"
        "📊 Impressions and clicks start counting from this moment.</blockquote>\n"
        "<i>💡 Manage, pause or remove it anytime from Ad Slots.</i>",
        reply_markup=kb([btn("📢 Open Ad Slots", "admin_ads", style="primary")]))


@router.callback_query(F.data.startswith("ad_tog:"))
async def cb_ad_tog(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("👑 Super admin only — ad slots are managed by the owner.", show_alert=True)
        return
    ad_id = call.data.split(":", 1)[1]
    ad = await get_ad(ad_id)
    paused = bool(ad and ad.get("active"))
    if ad:
        await set_active(ad_id, not ad.get("active"))
        await log_action(call.from_user.id, "ad_toggle", f"{ad_id}={'off' if ad.get('active') else 'on'}")
    await call.answer("⏸ Paused — this ad is off the dashboard." if paused
                      else "▶️ Resumed — this ad is back in rotation.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ad_del:"))
async def cb_ad_del(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("👑 Super admin only — ad slots are managed by the owner.", show_alert=True)
        return
    ad_id = call.data.split(":", 1)[1]
    await delete_ad(ad_id)
    await log_action(call.from_user.id, "ad_delete", ad_id)
    await call.answer("🗑 Ad removed — it's gone from the dashboard for good.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)
