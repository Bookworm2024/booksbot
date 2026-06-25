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

from config import ADMIN_IDS
from utils.audit import log_action
from utils.dedupe import clean_group, duplicate_groups
from utils.files import get_file
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def _panel():
    groups = await duplicate_groups(12)
    if not groups:
        return ("🧹 <b>Duplicates</b>\n\nNo title-duplicate files found. 🎉",
                kb([btn("🔄 Refresh", "admin_dedupe", style="primary")],
                   [btn("🔙 More Tools", "admin_more", style="primary")]))
    removable = sum(g["count"] - 1 for g in groups)
    lines = [f"🧹 <b>Duplicate Files</b> — {len(groups)} group(s), "
             f"~{removable} removable\n━━━━━━━━━━━━━━━━━━",
             "Tap a group to keep the best copy &amp; delete the rest:"]
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
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dd_clean:"))
async def cb_clean(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Access denied", show_alert=True)
        return
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f or not f.get("name_lc"):
        await call.answer("That group is already gone.", show_alert=True)
    else:
        removed = await clean_group(f["name_lc"])
        await log_action(call.from_user.id, "dedupe", f"{f.get('name','')[:40]} -{removed}")
        await call.answer(f"🧹 Removed {removed} duplicate(s).", show_alert=True)
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)
