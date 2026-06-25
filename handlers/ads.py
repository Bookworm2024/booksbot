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
from utils.keyboards import btn, kb, url_btn

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
        await call.answer("This offer has ended.", show_alert=True)
        return
    await call.answer()
    await bump_click(ad_id)
    rows = []
    if ad.get("url"):
        rows.append([url_btn("🔗 Open", ad["url"], style="success")])
    rows.append([btn("🔙 Menu", "menu_home", style="danger")])
    await call.message.edit_text(
        f"📢 <b>Sponsored</b>\n━━━━━━━━━━━━━━━━━━\n{escape(ad.get('text',''))}",
        reply_markup=kb(*rows), disable_web_page_preview=False)


# ── admin: ad slots ───────────────────────────────────────────────────────────
class AdFSM(StatesGroup):
    text = State()
    url = State()
    label = State()
    weight = State()


async def _panel():
    ads = await all_ads()
    lines = ["📢 <b>Ad Slots</b>\n━━━━━━━━━━━━━━━━━━"]
    if not ads:
        lines.append("<i>No ads yet.</i>")
    rows = []
    for a in ads:
        on = a.get("active")
        ctr = (100.0 * int(a.get("clicks") or 0) / int(a.get("impressions") or 1))
        lines.append(
            f"{'🟢' if on else '🔴'} <b>{escape(a.get('label','📢'))}</b> "
            f"(w{a.get('weight',1)}) · 👁 {int(a.get('impressions') or 0)} · "
            f"🖱 {int(a.get('clicks') or 0)} ({ctr:.0f}%)")
        rows.append([btn(f"{'🔴 Pause' if on else '🟢 Resume'} {a.get('label','')[:16]}",
                         f"ad_tog:{a['ad_id']}", style="danger" if on else "success"),
                     btn("🗑", f"ad_del:{a['ad_id']}", style="danger")])
    rows.append([btn("➕ New Ad", "ad_new", style="success")])
    rows.append([btn("🔙 More Tools", "admin_more", style="primary")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_ads")
async def cb_ads(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "ad_new")
async def cb_ad_new(call: CallbackQuery, state: FSMContext) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdFSM.text)
    await call.message.answer("📢 <b>New Ad</b>\n\nSend the ad <b>message text</b>. /cancel to abort.")


@router.message(AdFSM.text, F.text)
async def on_ad_text(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.update_data(ad_text=raw)
    await state.set_state(AdFSM.url)
    await message.answer("🔗 Send a <b>link URL</b> for the ad, or <code>skip</code> for none.")


@router.message(AdFSM.url, F.text)
async def on_ad_url(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    url = "" if raw.lower() == "skip" else raw
    if url and not url.lower().startswith(("http://", "https://", "tg://", "https://t.me")):
        await message.answer("⚠️ Enter a valid http(s)/tg link, or <code>skip</code>.")
        return
    await state.update_data(ad_url=url)
    await state.set_state(AdFSM.label)
    await message.answer("🏷 Send a short <b>button label</b> (e.g. <code>🔥 50% Off Today</code>).")


@router.message(AdFSM.label, F.text)
async def on_ad_label(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.update_data(ad_label=raw[:40])
    await state.set_state(AdFSM.weight)
    await message.answer("⚖️ Send a <b>weight 1–10</b> (higher = shown more often).")


@router.message(AdFSM.weight, F.text)
async def on_ad_weight(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    if not raw.isdigit() or not (1 <= int(raw) <= 10):
        await message.answer("⚠️ Enter a whole number 1–10.")
        return
    data = await state.get_data()
    await state.clear()
    ad_id = await create_ad(data.get("ad_text", ""), data.get("ad_url", ""),
                            data.get("ad_label", "📢 Sponsored"), int(raw), message.chat.id)
    await log_action(message.chat.id, "ad_create", ad_id)
    await message.answer(
        f"✅ <b>Ad created</b> (<code>{ad_id}</code>) — live on the dashboard now.",
        reply_markup=kb([btn("📢 Ad Slots", "admin_ads", style="primary")]))


@router.callback_query(F.data.startswith("ad_tog:"))
async def cb_ad_tog(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    ad_id = call.data.split(":", 1)[1]
    ad = await get_ad(ad_id)
    if ad:
        await set_active(ad_id, not ad.get("active"))
        await log_action(call.from_user.id, "ad_toggle", f"{ad_id}={'off' if ad.get('active') else 'on'}")
    await call.answer("Updated")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ad_del:"))
async def cb_ad_del(call: CallbackQuery) -> None:
    if not _super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    ad_id = call.data.split(":", 1)[1]
    await delete_ad(ad_id)
    await log_action(call.from_user.id, "ad_delete", ad_id)
    await call.answer("Deleted")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)
