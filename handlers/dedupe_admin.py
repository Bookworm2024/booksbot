"""
handlers/dedupe_admin.py — admin duplicate-file cleanup.

Admin → 🧰 More Tools → 🧹 Duplicates: lists title-duplicate groups; one tap
removes the extras (keeping the best-deliverable copy). Admin-reviewed so
legitimately-different same-title editions aren't auto-purged.
"""
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from utils.audit import log_action
from utils.dedupe import clean_group, duplicate_groups
from utils.files import get_file
from utils.keyboards import btn, kb
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()


async def _panel():
    groups = await duplicate_groups(12)
    if not groups:
        return ("✨ <b>Duplicates</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "<i>Your archive is spotless.</i>\n\n"
                "<blockquote>✅ No title-duplicate files found — every title earns its "
                "place on the shelf. Pop back anytime to keep things tidy.</blockquote>",
                kb([btn("🔄 Check Again", "admin_dedupe", style="primary")],
                   [btn("🔙 More Tools", "admin_more", style="primary")]))
    removable = sum(g["count"] - 1 for g in groups)
    lines = ["🧹 <b>Duplicate Cleanup</b>\n"
             "━━━━━━━━━━━━━━━━━━",
             "<i>Tidy the archive without losing a single read.</i>\n",
             f"<blockquote>📊 <b>Duplicate groups</b> · <code>{len(groups)}</code>\n"
             f"🗑 <b>Removable copies</b> · <code>~{removable}</code></blockquote>\n",
             "<blockquote>Tap a group below and we'll keep the <b>best-deliverable</b> "
             "copy, then quietly remove the rest. Genuinely different same-title "
             "editions stay safe — nothing is purged without your tap.</blockquote>",
             "<i>💡 Pick a group to clean it up.</i>"]
    rows = []
    for g in groups:
        rep = g["ids"][0] if g["ids"] else ""
        rows.append([btn(f"🧹 {g['name'][:26]} ×{g['count']}",
                         f"dd_clean:{rep}", style="danger")])
    rows.append([btn("🔄 Refresh", "admin_dedupe", style="primary"),
                 btn("🔙 More Tools", "admin_more", style="primary")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "admin_dedupe")
async def cb_dedupe(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dd_clean:"))
async def cb_clean(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f or not f.get("name_lc"):
        await call.answer("That group has already been cleaned — nothing left to remove.", show_alert=True)
    else:
        removed = await clean_group(f["name_lc"])
        await log_action(call.from_user.id, "dedupe", f"{f.get('name','')[:40]} -{removed}")
        await call.answer(f"✨ Done — kept the best copy, removed {removed} duplicate(s).", show_alert=True)
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)
