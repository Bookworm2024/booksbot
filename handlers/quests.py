"""
handlers/quests.py — growth quests board (share-to-earn / invite quests).

Account → 🚀 Quests (also /quests). One-time bounties for sharing the bot,
referring friends, levelling up and playing. Completed quests show a Claim
button; the share quest unlocks once you use the share feature.
"""
import logging
from urllib.parse import quote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import BOT_USERNAME
from utils.format import fmt_amount
from utils.keyboards import btn, kb, url_btn
from utils.quests import claim as claim_quest, mark_shared, status

logger = logging.getLogger(__name__)
router = Router()


def _bar(have: int, target: int, width: int = 10) -> str:
    filled = 0 if target <= 0 else max(0, min(width, have * width // target))
    return "🟩" * filled + "⬜" * (width - filled)


def _share_url(uid: int) -> str:
    msg = (f"📚 Free books, audiobooks & games on @{BOT_USERNAME}! "
           "Read, play and earn rewards. Join me:")
    return (f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={uid}"
            f"&text={quote(msg)}")


async def _view(uid: int):
    items = await status(uid)
    lines = ["<b>🚀 Growth Quests</b>", "━━━━━━━━━━━━━━━━━━",
             "<i>One-time bounties — claim when complete.</i>", ""]
    rows = []
    for q in items:
        tick = "✅" if q["done"] else "⬜"
        claimed = " · 🎁 claimed" if q["claimed"] else ""
        lines.append(
            f"{tick} {q['emoji']} <b>{q['title']}</b> — {q['desc']}\n"
            f"   {_bar(q['have'], q['target'])} {q['have']}/{q['target']}"
            f" · <b>+{fmt_amount(q['reward'])} BGM</b>{claimed}")
        if q["claimable"]:
            rows.append([btn(f"🎁 Claim {q['title']} (+{fmt_amount(q['reward'])} BGM)",
                             f"quest_claim:{q['key']}", style="success")])
    rows.append([url_btn("📤 Share the Bot", _share_url(uid), style="success"),
                 btn("🎁 Loot Crates", "menu_crates", style="primary")])
    rows.append([btn("🎁 Refer & Earn", "acc_refer", style="primary"),
                 btn("🔙 Account", "menu_account", style="danger")])
    return "\n".join(lines), kb(*rows)


@router.callback_query(F.data == "menu_quests")
async def cb_quests(call: CallbackQuery) -> None:
    await call.answer()
    # opening the board (which carries the Share button) unlocks the share quest
    await mark_shared(call.from_user.id)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("quests"))
async def cmd_quests(message: Message) -> None:
    await mark_shared(message.chat.id)
    text, markup = await _view(message.chat.id)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("quest_claim:"))
async def cb_claim(call: CallbackQuery) -> None:
    qkey = call.data.split(":", 1)[1]
    paid = await claim_quest(call.from_user.id, qkey)
    if paid > 0:
        await call.answer(f"🎉 +{fmt_amount(paid)} BGM!", show_alert=True)
    else:
        await call.answer("Not claimable yet.", show_alert=True)
    text, markup = await _view(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
