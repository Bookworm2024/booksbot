"""
handlers/referral.py — Refer & Earn.

  /refer (or 🎁 Refer & Earn) — your link + total referrals + leaderboard
The actual reward logic lives in utils/referral.py and fires from start.py
when a referred user clears the join-gate.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import BOT_USERNAME
from database.connection import MongoManager
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


async def _refer_view(uid: int):
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"ref_count": 1}) or {}
    count = int(doc.get("ref_count") or 0)
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    text = (
        "<b>🎁 Refer &amp; Earn</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Invite friends and earn <b>+0.5 BGM</b> each (they get <b>+0.25 BGM</b>).\n"
        "Reward pays out once they join the required channels.\n\n"
        f"🔗 <b>Your link:</b>\n<code>{link}</code>\n\n"
        f"📊 <b>Successful referrals:</b> <b>{count}</b>"
    )
    return text, kb([btn("🏆 Leaderboard", "ref_leaderboard", style="primary"),
                     btn("🚀 Quests", "menu_quests", style="success")],
                    [btn("🔙 Back", "menu_account", style="danger")])


@router.message(Command("refer"))
async def cmd_refer(message: Message) -> None:
    text, markup = await _refer_view(message.chat.id)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "acc_refer")
async def cb_refer(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _refer_view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "ref_leaderboard")
async def cb_leaderboard(call: CallbackQuery) -> None:
    await call.answer()
    db = await MongoManager.get()
    top = await db.find_global("users", {"ref_count": {"$gt": 0}},
                               sort=[("ref_count", -1)], limit=10,
                               proj={"user_id": 1, "first_name": 1, "ref_count": 1})
    if not top:
        body = "No referrals yet — be the first! 🚀"
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        body = "\n".join(
            f"{medals[i]} {(t.get('first_name') or 'User')[:18]} — <b>{int(t.get('ref_count',0))}</b>"
            for i, t in enumerate(top))
    await call.message.edit_text(
        "<b>🏆 Referral Leaderboard</b>\n━━━━━━━━━━━━━━━━━━\n" + body,
        reply_markup=kb([btn("🔙 Back", "acc_refer", style="danger")]))
