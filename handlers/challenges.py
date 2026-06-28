"""
handlers/challenges.py — monthly reading challenges board.

Library → 🔭 Discover → 🎯 Challenges (also /challenges). Shows each monthly
goal with a progress bar; completed goals get a one-tap Claim for their BGM
reward. Counters are maintained centrally in utils.missions.mark via
utils.challenges.bump.
"""
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from utils.challenges import claim as claim_challenge, status
from utils.format import fmt_amount
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


def _bar(have: int, target: int, width: int = 10) -> str:
    filled = 0 if target <= 0 else max(0, min(width, have * width // target))
    return "🟩" * filled + "⬜" * (width - filled)


async def _view(uid: int):
    items = await status(uid)
    month = datetime.now(timezone.utc).strftime("%B %Y")
    done_count = sum(1 for c in items if c["done"])
    lines = [f"🎯 <b>Reading Challenges</b>",
             f"<i>Your goals for {month} — clear them, claim the 💎 BGM.</i>",
             "━━━━━━━━━━━━━━━━━━"]
    body = []
    rows = []
    total_claimable = 0.0
    for c in items:
        tick = "✅" if c["done"] else "⬜"
        claimed = " · <i>🎁 reward banked</i>" if c["claimed"] else ""
        body.append(
            f"{tick} {c['emoji']} <b>{c['title']}</b> — <i>{c['desc']}</i>\n"
            f"   {_bar(c['have'], c['target'])} <code>{c['have']}/{c['target']}</code>"
            f" · <b>+{fmt_amount(c['reward'])} 💎 BGM</b>{claimed}")
        if c["claimable"]:
            total_claimable += c["reward"]
            rows.append([btn(f"🎁 Claim {c['title']} · +{fmt_amount(c['reward'])} BGM",
                             f"chal_claim:{c['key']}", style="success")])
    lines.append("<blockquote expandable>" + "\n".join(body) + "</blockquote>")
    if total_claimable > 0:
        lines.append(
            f"\n✨ <b>{fmt_amount(total_claimable)} 💎 BGM</b> ready to claim — "
            "<i>tap a reward below to bank it.</i>")
    elif done_count == len(items):
        lines.append(
            "\n👑 <i>Every challenge cleared this month — masterful. "
            "Fresh goals arrive when the month turns.</i>")
    else:
        lines.append(
            "\n<i>💡 Keep reading, playing and spinning — each milestone unlocks "
            "its 💎 BGM the moment you hit the target.</i>")
    rows.append([btn("🔭 Discover", "lib_discover", style="primary"),
                 btn("🔙 Library", "menu_library", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "menu_challenges")
async def cb_challenges(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.message(Command("challenges"))
async def cmd_challenges(message: Message) -> None:
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("chal_claim:"))
async def cb_claim(call: CallbackQuery) -> None:
    ckey = call.data.split(":", 1)[1]
    paid = await claim_challenge(call.from_user.id, ckey)
    if paid > 0:
        await call.answer(
            f"🎉 Challenge complete! +{fmt_amount(paid)} BGM added to your wallet.",
            show_alert=True)
    else:
        await call.answer(
            "This reward isn't ready yet — finish the goal first, or it may "
            "already be banked.",
            show_alert=True)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)
