"""
handlers/stats.py — global analytics (/stats and the Bot Tools button).

Aggregates across all Mongo clusters: users, archive size, downloads,
request outcomes, and tokens in circulation.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


async def _sum(field: str) -> float:
    """Sum a numeric user field across every cluster."""
    db = await MongoManager.get()
    total = 0.0
    for idx in db.healthy:
        cur = db.dbs[idx]["users"].aggregate(
            [{"$group": {"_id": None, "t": {"$sum": f"${field}"}}}])
        async for row in cur:
            total += float(row.get("t") or 0)
    return total


async def _build() -> str:
    db = await MongoManager.get()
    # Total real users the bot knows about — every row in `users`, whether they
    # joined through this bot or were imported from the legacy bot. This is the
    # same audience the broadcast/reminder workers target.
    users = await db.count_global("users")
    imported = await db.count_global("users", {"imported": True})
    organic = users - imported
    files = await db.count_global("files")
    pend = await db.count_global("requests", {"status": "pending"})
    done = await db.count_global("requests", {"status": "fulfilled"})
    canc = await db.count_global("requests", {"status": "cancelled"})
    downloads = await _sum("downloads")
    bgm = await _sum("bookgem")
    bcn = await _sum("bookcoin")
    # Only show the organic/imported split once a legacy import has happened.
    users_block = f"👥 <b>Users:</b> <code>{users:,}</code>\n"
    if imported:
        users_block += (f"   └ 🌱 Organic: <code>{organic:,}</code> · "
                        f"📦 Imported: <code>{imported:,}</code>\n")
    return (
        "<b>📊 Bot Analytics</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + users_block +
        f"📚 <b>Archive files:</b> <code>{files:,}</code>\n"
        f"📥 <b>Downloads:</b> <code>{int(downloads):,}</code>\n\n"
        "<b>📨 Requests</b>\n"
        f"⏳ Pending: <code>{pend}</code> · ✅ Fulfilled: <code>{done}</code> · "
        f"❌ Cancelled: <code>{canc}</code>\n\n"
        "<b>💰 Tokens in circulation</b>\n"
        f"💎 BGM: <code>{bgm:,.2f}</code> · 🪙 BCN: <code>{bcn:,.2f}</code>"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await message.answer(await _build())


@router.callback_query(F.data == "tool_stats")
async def cb_stats(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        await _build(), reply_markup=kb([btn("🔙 Back", "menu_tools", style="danger")]))
