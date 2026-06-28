"""
handlers/cosmetics.py — 🎨 Customize: flair shop · vanity handle · Reading DNA.

Profile → 🎨 Customize:
  ✨ Flair Shop    — buy/equip decorative profile flair with BGM (a BGM sink)
  ✍️ Vanity Handle — set a custom display name (costs BGM; validated)
  🧬 Reading DNA   — your genre breakdown, from your favorites' tags
"""
import logging
import re
from collections import Counter

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.cosmetics import FLAIRS, buy, equip, owned
from utils.format import fmt_amount
from utils.keyboards import btn, kb
from utils.wallet import charge_bgm

logger = logging.getLogger(__name__)
router = Router()

_VANITY_COST = 10.0
_VANITY_RE = re.compile(r"^[A-Za-z0-9 _]{3,20}$")


class CosFSM(StatesGroup):
    vanity = State()


@router.callback_query(F.data == "acc_customize")
async def cb_customize(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        "🎨 <b>Customise Your Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Make your reading identity unmistakably yours.</i>\n\n"
        "<blockquote>"
        "🪄 <b>Personalise everything</b>\n"
        "✨ <b>Flair Shop</b> — collectible emblems that sit beside your name.\n"
        "✍️ <b>Vanity Handle</b> — a custom display name, only yours.\n"
        "🧬 <b>Reading DNA</b> — your taste, mapped from the books you love."
        "</blockquote>\n"
        "<i>💡 Flair and your handle show on your profile and the leaderboards.</i>",
        reply_markup=kb([btn("✨ Flair Shop", "cos_shop", style="success")],
                        [btn("✍️ Vanity Handle", "cos_vanity", style="primary")],
                        [btn("🧬 Reading DNA", "cos_dna", style="primary")],
                        [btn("🔙 Back to Profile", "acc_profile", style="danger")]))


# ── flair shop ───────────────────────────────────────────────────────────────
@router.callback_query(F.data == "cos_shop")
async def cb_shop(call: CallbackQuery, answer: bool = True) -> None:
    # `answer=False` when re-rendered from buy/equip, which already answered the
    # callback (a CallbackQuery can only be answered once).
    if answer:
        await call.answer()
    uid = call.from_user.id
    own = await owned(uid)
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": uid},
                                 {"equipped_flair_id": 1, "bookgem": 1}) or {}
    eq = d.get("equipped_flair_id") or "none"
    bgm = float(d.get("bookgem") or 0)
    rows = []
    for f in FLAIRS:
        if f["id"] in own:
            tag = " · equipped" if f["id"] == eq else " · tap to equip"
            rows.append([btn(("✅ " if f["id"] == eq else "👜 ") + f["label"] + tag,
                             f"cos_eq:{f['id']}", style="success" if f["id"] == eq else "primary")])
        else:
            rows.append([btn(f"{f['label']} · {fmt_amount(f['price'])} BGM", f"cos_buy:{f['id']}",
                             style="primary")])
    rows.append([btn("🔙 Back to Customise", "acc_customize", style="danger")])
    await call.message.edit_text(
        "✨ <b>Flair Shop</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Collectible emblems that crown your name across the bot.</i>\n\n"
        f"💼 Your balance · <b>{fmt_amount(bgm)} 💎 BGM</b>\n\n"
        "<blockquote>"
        "🪄 <b>How flair works</b>\n"
        "🛒 Buy any emblem once with 💎 BGM — it's yours to keep, forever.\n"
        "👜 Switch freely between everything you own — no extra cost.\n"
        "👤 Your equipped flair appears beside your name on your profile."
        "</blockquote>\n"
        "<i>💡 Tap a price to buy, or an owned emblem to equip it instantly.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("cos_buy:"))
async def cb_buy(call: CallbackQuery) -> None:
    fid = call.data.split(":", 1)[1]
    ok, reason = await buy(call.from_user.id, fid)
    if ok:
        await equip(call.from_user.id, fid)
        await call.answer("✨ Unlocked and equipped — it's yours to keep. Wear it with pride!")
    else:
        await call.answer({"insufficient": "Not quite enough BGM for this one yet — top up or claim your daily BCN, then try again.",
                           "owned": "You already own this emblem — just tap it to equip.",
                           "free": "That one's free — no purchase needed.",
                           "unknown": "We couldn't find that emblem. Try another."}
                          .get(reason, "That purchase didn't go through. Please try again."), show_alert=True)
    await cb_shop(call, answer=False)


@router.callback_query(F.data.startswith("cos_eq:"))
async def cb_eq(call: CallbackQuery) -> None:
    ok = await equip(call.from_user.id, call.data.split(":", 1)[1])
    await call.answer("✨ Equipped — looking sharp." if ok
                      else "You don't own that emblem yet — pick it up in the Flair Shop first.",
                      show_alert=not ok)
    await cb_shop(call, answer=False)


# ── vanity handle ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "cos_vanity")
async def cb_vanity(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": call.from_user.id}, {"vanity": 1}) or {}
    await state.set_state(CosFSM.vanity)
    await call.message.edit_text(
        "✍️ <b>Vanity Handle</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Claim a custom display name that's unmistakably yours.</i>\n\n"
        f"👤 Current handle · <b>{d.get('vanity') or '—'}</b>\n\n"
        "<blockquote>"
        "✍️ <b>Choosing your handle</b>\n"
        "✅ 3–20 characters · letters, numbers, spaces or underscores.\n"
        f"💎 One-time cost · <code>{fmt_amount(_VANITY_COST)} BGM</code>.\n"
        "👀 Shows on your profile and across the leaderboards."
        "</blockquote>\n"
        "Send your new name below to claim it — or send <code>/cancel</code> to keep your current one.")


@router.message(CosFSM.vanity, F.text)
async def on_vanity(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ No changes made — your current handle stays just as it is.")
        return
    await state.clear()
    if not _VANITY_RE.match(raw):
        await message.answer(
            "⚠️ <b>That handle won't work</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Please use 3–20 characters — letters, numbers, spaces or underscores only.</i>\n\n"
            "Tap ✍️ Vanity Handle again and send a new name when you're ready.")
        return
    db = await MongoManager.get()
    # charge_bgm combines BGM across clusters (never falsely "insufficient" on a
    # split balance) and rolls back on a partial debit; then set the handle.
    if await charge_bgm(message.chat.id, _VANITY_COST):
        await db.safe_update("users", {"user_id": message.chat.id}, {"$set": {"vanity": raw}})
        await message.answer(
            "✅ <b>Handle claimed</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"You're now known as <b>{raw}</b> across the bot.\n"
            f"<i>💎 {fmt_amount(_VANITY_COST)} BGM deducted.</i>\n\n"
            "Open your profile to see your new look.",
            reply_markup=kb([btn("👤 View My Profile", "acc_profile", style="primary")]))
    else:
        await message.answer(
            "❌ <b>Not enough BGM</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>A vanity handle costs <code>{fmt_amount(_VANITY_COST)} BGM</code>.</i>\n\n"
            "Top up your wallet or claim your daily BCN, then try again — your name's waiting.")


# ── reading DNA ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "cos_dna")
async def cb_dna(call: CallbackQuery) -> None:
    await call.answer()
    db = await MongoManager.get()
    favs = await db.find_global("favorites", {"user_id": call.from_user.id},
                                limit=500, proj={"file_unique_id": 1})
    text = ("🧬 <b>Reading DNA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Your reading taste, mapped from the books you love most.</i>\n\n")
    if not favs:
        text += ("<blockquote>Your DNA is still forming. ⭐ Favourite a few books and "
                 "we'll chart the genres that define your taste.</blockquote>")
    else:
        fuids = [f["file_unique_id"] for f in favs][:500]
        files = await db.find_global("files", {"file_unique_id": {"$in": fuids}},
                                     proj={"genre": 1})
        counts = Counter((f.get("genre") or "Untagged") for f in files)
        if not counts:
            text += ("<blockquote>Your favourites aren't genre-tagged yet — we're still "
                     "reading them. Check back soon to see your profile take shape.</blockquote>")
        else:
            total = sum(counts.values())
            text += "<blockquote>"
            for genre, n in counts.most_common(8):
                pct = int(n / total * 100)
                bars = pct // 10
                text += f"{'🟪' * bars}{'⬜' * (10 - bars)} <b>{genre}</b> · {pct}%\n"
            text += "</blockquote>\n<i>💡 Keep favouriting books to refine your taste profile.</i>"
    await call.message.edit_text(text, reply_markup=kb([btn("🔙 Back to Customise", "acc_customize", style="danger")]))
