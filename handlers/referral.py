"""
handlers/referral.py — Refer & Earn.

  /refer (or 🎁 Refer & Earn) — your link + total referrals + leaderboard
The actual reward logic lives in utils/referral.py and fires from start.py
when a referred user clears the join-gate.
"""
import logging
from html import escape

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
        "🎁 <b>Refer &amp; Earn</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Share the library you love — and we'll reward you for it.</i>\n"
        "<blockquote>"
        "💎 You earn <b>+0.5 BGM</b> for every friend who joins.\n"
        "🎁 They start with a <b>+0.25 BGM</b> welcome gift.\n"
        "✨ Rewards land the moment they clear the join-gate.\n"
        "🏆 Hit referral milestones for bonus BGM along the way."
        "</blockquote>\n"
        "🔗 <b>Your personal invite link</b>\n"
        f"<code>{link}</code>\n"
        "<i>Tap to copy, then share it anywhere.</i>\n\n"
        f"📊 <b>Verified referrals:</b> <code>{count}</code>\n"
        "<i>💡 Climb the contest and leaderboard to earn even more.</i>"
    )
    return text, kb([btn("🏁 Monthly Contest", "ref_contest", style="success")],
                    [btn("🏆 Leaderboard", "ref_leaderboard", style="primary"),
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


@router.callback_query(F.data == "ref_contest")
async def cb_contest(call: CallbackQuery) -> None:
    await call.answer()
    from utils.contests import PRIZES, my_stats, settle, this_month, top_month
    from utils.format import fmt_amount
    await settle(call.bot)  # lazily pay out last month's winners if due
    month = this_month()
    top = await top_month(month, 10)
    db = await MongoManager.get()
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"🏁 <b>Monthly Referral Contest</b> · <code>{month}</code>",
             "━━━━━━━━━━━━━━━━━━━━",
             "<i>Invite the most friends this month and the podium takes home BGM.</i>",
             "🏆 <b>Prize pool</b> — 🥇 <code>%s</code> · 🥈 <code>%s</code> · 🥉 <code>%s</code> 💎 BGM" % tuple(fmt_amount(p) for p in PRIZES),
             "━━━━━━━━━━━━━━━━━━━━"]
    if not top:
        lines.append("<blockquote>🚀 The board is wide open — no referrals yet this month.\nShare your link now and claim the top spot.</blockquote>")
    else:
        lines.append("👑 <b>This month's leaders</b>")
        for i, t in enumerate(top):
            u = await db.find_one_global("users", {"user_id": t.get("user_id")},
                                         {"first_name": 1}) or {}
            who = escape((u.get("first_name") or "User")[:18])
            lines.append(f"{medals[i]} {who} — <code>{int(t.get('count') or 0)}</code>")
    mine, rank = await my_stats(call.from_user.id, month)
    if mine:
        lines.append(f"\n👤 <b>Your standing:</b> <code>{mine}</code> referral(s) · rank <code>#{rank}</code>\n<i>💡 Keep sharing — every join climbs you higher.</i>")
    await call.message.edit_text(
        "\n".join(lines),
        reply_markup=kb([btn("🔄 Refresh", "ref_contest", style="primary")],
                        [btn("🔙 Back", "acc_refer", style="danger")]))


@router.callback_query(F.data == "ref_leaderboard")
async def cb_leaderboard(call: CallbackQuery) -> None:
    await call.answer()
    db = await MongoManager.get()
    top = await db.find_global("users", {"ref_count": {"$gt": 0}},
                               sort=[("ref_count", -1)], limit=10,
                               proj={"user_id": 1, "first_name": 1, "ref_count": 1})
    if not top:
        body = "<blockquote>🚀 No champions yet — this hall of fame is waiting for its first name.\nShare your link and be the one to set the pace.</blockquote>"
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
        body = "👑 <b>All-time top inviters</b>\n" + "\n".join(
            f"{medals[i]} {escape((t.get('first_name') or 'User')[:18])} — <code>{int(t.get('ref_count',0))}</code>"
            for i, t in enumerate(top))
    await call.message.edit_text(
        "🏆 <b>Referral Leaderboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Our most generous members — the readers who keep growing the library.</i>\n\n" + body +
        "\n\n<i>💡 Every verified friend moves you up the ranks.</i>",
        reply_markup=kb([btn("🔙 Back", "acc_refer", style="danger")]))
