"""
handlers/harvester_admin.py — admin control for the public-domain book harvester.

Admin → 🧰 More Tools → 📚 Harvester: see live status (on/off, this week's count vs
cap, lifetime total, sources, cursor), toggle it on/off, and send the weekly
digest on demand. Fine-tuning (weekly cap, pace, max file size) lives in
⚙️ Live Pricing under the "Harvester" category. Super-admin only.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils import harvester
from utils.keyboards import btn, kb
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()


async def _panel() -> tuple[str, object]:
    st = await harvester.status()
    on = st["enabled"]
    chan = await __chan()
    cap = st["cap"]
    week = st["week_count"]
    last = st.get("last_report") or "—"
    if isinstance(last, str) and "T" in last:
        last = last.split("T")[0]
    chan_line = (f"🗂 <b>File channel:</b> <code>{chan}</code>" if chan
                 else "🗂 <b>File channel:</b> <i>not set — harvester idles until you set one</i>")
    text = (
        "📚 <b>Archive Harvester</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Grows the library on autopilot from public-domain archives "
        "(Project Gutenberg + Standard Ebooks). Fully background; you get a weekly digest.</i>\n"
        "<blockquote>"
        f"⚙️ <b>Status:</b> {'🟢 ON' if on else '🔴 OFF'}\n"
        f"📥 <b>This week:</b> <code>{week}</code> / <code>{cap}</code>\n"
        f"📦 <b>Lifetime harvested:</b> <code>{st['total']}</code>\n"
        f"🌐 <b>Sources:</b> {escape(', '.join(st.get('sources', [])) or '—')}\n"
        f"🔖 <b>Gutenberg cursor:</b> page <code>{st['page']}</code>\n"
        f"🗓 <b>Last digest:</b> {escape(str(last))}\n"
        f"{chan_line}"
        "</blockquote>\n"
        "<i>💡 Tune cap / pace / max size in ⚙️ Live Pricing → Harvester. Only legal, "
        "redistributable public-domain sources are used.</i>"
    )
    rows = [
        [btn("🔴 Turn OFF", "harvest_toggle", style="danger") if on
         else btn("🟢 Turn ON", "harvest_toggle", style="success")],
        [btn("📨 Send Digest Now", "harvest_report", style="primary"),
         btn("🔄 Refresh", "admin_harvest", style="primary")],
        [btn("⚙️ Tune in Live Pricing", "admin_pricing", style="primary")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return text, kb(*rows)


async def __chan() -> int:
    from utils.channel import get_file_channel
    return await get_file_channel()


@router.callback_query(F.data == "admin_harvest")
async def cb_harvest(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "harvest_toggle")
async def cb_toggle(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    now_on = not await harvester.enabled()
    await harvester.set_enabled(now_on)
    await call.answer("🟢 Harvester ON — it'll pull in the background." if now_on
                      else "🔴 Harvester paused.", show_alert=True)
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "harvest_report")
async def cb_report(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer("📨 Sending the digest to all admins…")
    try:
        await harvester.report_now(call.bot)
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual harvest report failed: %s", exc)
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.message(Command("harvest"))
async def cmd_harvest(message: Message) -> None:
    if not is_super(message.chat.id):
        return
    text, markup = await _panel()
    await message.answer(text, reply_markup=markup)
