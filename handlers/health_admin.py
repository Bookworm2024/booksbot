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

from config import ADMIN_IDS
from utils.audit import log_action
from utils.errors import count as error_count, recent as recent_errors
from utils.keyboards import btn, kb
from utils.metrics import snapshot

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def _health_text() -> str:
    m = snapshot()
    errs = await recent_errors(5)
    total_err = await error_count()
    lines = [
        "🩺 <b>Health &amp; Metrics</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"⏱ Uptime: <b>{m['uptime']}</b>",
        f"📨 Updates: <b>{m['updates']:,}</b> "
        f"(💬 {m['messages']:,} · 🔘 {m['callbacks']:,})",
        f"⚠️ Errors (since boot): <b>{m['errors']:,}</b> · rate <b>{m['error_rate']}%</b>",
        f"🗃 Errors stored: <b>{total_err:,}</b>",
    ]
    if errs:
        lines.append("\n<b>Recent errors:</b>")
        for e in errs:
            at = e.get("at")
            ts = at.strftime("%d %b %H:%M") if hasattr(at, "strftime") else "—"
            where = escape((e.get("where") or "")[:40])
            msg = escape((e.get("message") or "")[:90])
            lines.append(f"<code>{ts}</code> · <b>{escape(e.get('type','?'))}</b>"
                         f"{(' @ ' + where) if where else ''}\n   <i>{msg}</i>")
    else:
        lines.append("\n<i>No errors captured. 🎉</i>")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_health")
async def cb_health(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _health_text(),
        reply_markup=kb([btn("🔄 Refresh", "admin_health", style="primary"),
                         btn("📦 Backup Now", "admin_backup", style="success")],
                        [btn("🔙 More Tools", "admin_more", style="primary")]))


@router.callback_query(F.data == "admin_backup")
async def cb_backup(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer("Building backup…")
    from utils.backup import backup_channel, backup_now
    if not await backup_channel():
        await call.message.answer(
            "📦 <b>No backup channel set.</b>\nSet kv <code>backup_channel</code> "
            "or a LOG_CHANNEL_ID so I can post backups there.")
        return
    try:
        summary = await backup_now(call.bot)
    except Exception as exc:  # noqa: BLE001
        from utils.errors import capture
        await capture(exc, "admin_backup")
        await call.message.answer(f"⚠️ Backup failed: {escape(str(exc)[:120])}")
        return
    await log_action(call.from_user.id, "backup", str(summary))
    await call.message.answer(
        "✅ <b>Backup posted</b> to the backup channel.\n"
        + " · ".join(f"{k}: {v}" for k, v in summary.items()))
