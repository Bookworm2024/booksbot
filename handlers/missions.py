"""
handlers/missions.py — daily missions board.

🎯 Daily Missions → see today's tasks (✅/⬜) and claim the BGM you've earned.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.missions import MISSIONS, claim, status

logger = logging.getLogger(__name__)
router = Router()


async def _board(uid: int):
    st = await status(uid)
    done_n = len(st["done"])
    total_n = len(MISSIONS)
    lines = ["🎯 <b>Daily Missions</b>",
             "━━━━━━━━━━━━━━━━━━━━",
             "<i>Four quick wins, refreshed every day. Tick them off as you read, "
             "play and claim — then collect the 💎 BGM you've earned.</i>",
             ""]
    body = []
    for key, (label, reward) in MISSIONS.items():
        mark = "✅" if key in st["done"] else "⬜"
        tag = " · <i>🎁 claimed</i>" if key in st["claimed"] else ""
        body.append(f"{mark} {label} — <b>+{fmt_amount(reward)} 💎 BGM</b>{tag}")
    lines.append("<blockquote>" + "\n".join(body) + "</blockquote>")
    lines.append(f"📊 <i>Today's progress:</i> <code>{done_n}/{total_n}</code> done")
    if st["claimable"] > 0:
        lines.append(f"✨ <b>Ready to bank:</b> <code>{fmt_amount(st['claimable'])}</code> 💎 BGM "
                     "— tap <b>Claim</b> below.")
    else:
        lines.append("💡 <i>Complete a mission to unlock your reward — it lands the "
                     "moment you claim.</i>")
    rows = []
    if st["claimable"] > 0:
        rows.append([btn(f"🎁 Claim {fmt_amount(st['claimable'])} BGM",
                         "missions_claim", style="success")])
    rows.append([btn("🎮 Play a Game", "menu_games", style="primary"),
                 btn("🔙 Back", "menu_home", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.message(Command("missions"))
async def cmd_missions(message: Message) -> None:
    text, markup = await _board(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu_missions")
async def cb_missions(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _board(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "missions_claim")
async def cb_claim(call: CallbackQuery) -> None:
    got = await claim(call.from_user.id)
    await call.answer(
        f"✨ Nice work! +{fmt_amount(got)} BGM added to your wallet."
        if got else "⏳ Nothing to claim yet — complete a mission first, then come back.",
        show_alert=bool(got))
    text, markup = await _board(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)
