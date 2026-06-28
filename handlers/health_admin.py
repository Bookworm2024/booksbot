"""
handlers/health_admin.py — admin 🩺 Health / error feed / backups.

Admin → 🧰 More Tools → 🩺 Health:
  • uptime + in-process metrics (updates/messages/callbacks/errors, error rate)
  • recent captured errors (utils.errors)
  • 📦 Backup Now (utils.backup) — export config/economy state to the backup channel
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from utils.audit import log_action
from utils.errors import count as error_count, recent as recent_errors
from utils.keyboards import btn, kb
from utils.metrics import snapshot
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()


async def _health_text() -> str:
    m = snapshot()
    errs = await recent_errors(5)
    total_err = await error_count()
    lines = [
        "🩺 <b>System Health</b>",
        "━━━━━━━━━━━━━━━━━━",
        "<i>A live pulse on the service powering your library.</i>",
        "",
        "<blockquote>"
        f"⏱ <b>Uptime</b> — running for <b>{m['uptime']}</b>\n"
        f"📨 <b>Updates</b> — <code>{m['updates']:,}</code> "
        f"(💬 <code>{m['messages']:,}</code> messages · 🔘 <code>{m['callbacks']:,}</code> taps)\n"
        f"⚠️ <b>Errors since boot</b> — <code>{m['errors']:,}</code> · "
        f"rate <code>{m['error_rate']}%</code>\n"
        f"🗃 <b>Errors on record</b> — <code>{total_err:,}</code>"
        "</blockquote>",
    ]
    if errs:
        lines.append("\n📋 <b>Most recent issues</b>")
        rows = []
        for e in errs:
            at = e.get("at")
            ts = at.strftime("%d %b %H:%M") if hasattr(at, "strftime") else "—"
            where = escape((e.get("where") or "")[:40])
            msg = escape((e.get("message") or "")[:90])
            rows.append(f"<code>{ts}</code> · <b>{escape(e.get('type','?'))}</b>"
                        f"{(' @ ' + where) if where else ''}\n   <i>{msg}</i>")
        lines.append("<blockquote expandable>" + "\n".join(rows) + "</blockquote>")
    else:
        lines.append("\n<blockquote>✨ <b>Running clean.</b> No errors captured — "
                     "everything is behaving exactly as it should.</blockquote>")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_health")
async def cb_health(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _health_text(),
        reply_markup=kb([btn("🔄 Refresh", "admin_health", style="primary"),
                         btn("📦 Backup Now", "admin_backup", style="success")],
                        [btn("🔙 More Tools", "admin_more", style="primary")]))


@router.callback_query(F.data == "admin_backup")
async def cb_backup(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer("Packaging your latest backup…")
    from utils.backup import backup_channel, backup_now
    if not await backup_channel():
        await call.message.answer(
            "📦 <b>No backup channel yet</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Tell me where to keep your safety copies.</i>\n\n"
            "<blockquote>Set the <code>backup_channel</code> key — or a "
            "<code>LOG_CHANNEL_ID</code> — and I'll post a full export of your "
            "config and economy state there on demand.</blockquote>")
        return
    try:
        summary = await backup_now(call.bot)
    except Exception as exc:  # noqa: BLE001
        from utils.errors import capture
        await capture(exc, "admin_backup")
        await call.message.answer(
            "⚠️ <b>Backup didn't complete</b>\n"
            f"<i>{escape(str(exc)[:120])}</i>\n\n"
            "Nothing was lost — give it another try, or check the backup channel "
            "is reachable.")
        return
    await log_action(call.from_user.id, "backup", str(summary))
    await call.message.answer(
        "✅ <b>Backup secured</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>A fresh snapshot is now saved to your backup channel.</i>\n\n"
        "<blockquote>"
        + "\n".join(f"📦 <b>{escape(str(k))}</b> — <code>{escape(str(v))}</code>"
                    for k, v in summary.items())
        + "</blockquote>")
