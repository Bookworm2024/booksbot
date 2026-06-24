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
        "🎨 <b>Customize</b>\n\nMake your profile yours.",
        reply_markup=kb([btn("✨ Flair Shop", "cos_shop", style="success")],
                        [btn("✍️ Vanity Handle", "cos_vanity", style="primary")],
                        [btn("🧬 Reading DNA", "cos_dna", style="primary")],
                        [btn("🔙 Back", "acc_profile", style="danger")]))


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
            tag = " — equipped" if f["id"] == eq else " — tap to equip"
            rows.append([btn(("✅ " if f["id"] == eq else "👜 ") + f["label"] + tag,
                             f"cos_eq:{f['id']}", style="success" if f["id"] == eq else "primary")])
        else:
            rows.append([btn(f"{f['label']} — {fmt_amount(f['price'])} BGM", f"cos_buy:{f['id']}",
                             style="primary")])
    rows.append([btn("🔙 Back", "acc_customize", style="danger")])
    await call.message.edit_text(
        f"✨ <b>Flair Shop</b>\n💎 Balance: <b>{fmt_amount(bgm)} BGM</b>\n\n"
        "Flair shows next to your name on your profile.",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("cos_buy:"))
async def cb_buy(call: CallbackQuery) -> None:
    fid = call.data.split(":", 1)[1]
    ok, reason = await buy(call.from_user.id, fid)
    if ok:
        await equip(call.from_user.id, fid)
        await call.answer("Purchased & equipped! 🎉")
    else:
        await call.answer({"insufficient": "Not enough BGM.", "owned": "You already own this.",
                           "free": "That one's free.", "unknown": "Unknown item."}
                          .get(reason, "Couldn't buy that."), show_alert=True)
    await cb_shop(call, answer=False)


@router.callback_query(F.data.startswith("cos_eq:"))
async def cb_eq(call: CallbackQuery) -> None:
    ok = await equip(call.from_user.id, call.data.split(":", 1)[1])
    await call.answer("Equipped ✨" if ok else "You don't own that.", show_alert=not ok)
    await cb_shop(call, answer=False)


# ── vanity handle ────────────────────────────────────────────────────────────
@router.callback_query(F.data == "cos_vanity")
async def cb_vanity(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    db = await MongoManager.get()
    d = await db.find_one_global("users", {"user_id": call.from_user.id}, {"vanity": 1}) or {}
    await state.set_state(CosFSM.vanity)
    await call.message.edit_text(
        f"✍️ <b>Vanity Handle</b>\nCurrent: <b>{d.get('vanity') or '—'}</b>\n\n"
        f"Send a new display name (3–20 letters/numbers/spaces). Costs "
        f"<b>{fmt_amount(_VANITY_COST)} BGM</b>. /cancel to abort.")


@router.message(CosFSM.vanity, F.text)
async def on_vanity(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.clear()
    if not _VANITY_RE.match(raw):
        await message.answer("⚠️ 3–20 characters — letters, numbers, spaces or underscores only.")
        return
    db = await MongoManager.get()
    # charge_bgm combines BGM across clusters (never falsely "insufficient" on a
    # split balance) and rolls back on a partial debit; then set the handle.
    if await charge_bgm(message.chat.id, _VANITY_COST):
        await db.safe_update("users", {"user_id": message.chat.id}, {"$set": {"vanity": raw}})
        await message.answer(f"✅ Vanity handle set to <b>{raw}</b> (−{fmt_amount(_VANITY_COST)} BGM).",
                             reply_markup=kb([btn("👤 Profile", "acc_profile", style="primary")]))
    else:
        await message.answer(f"❌ You need {fmt_amount(_VANITY_COST)} BGM to set a vanity handle.")


# ── reading DNA ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "cos_dna")
async def cb_dna(call: CallbackQuery) -> None:
    await call.answer()
    db = await MongoManager.get()
    favs = await db.find_global("favorites", {"user_id": call.from_user.id},
                                limit=500, proj={"file_unique_id": 1})
    text = "🧬 <b>Reading DNA</b>\n━━━━━━━━━━━━━━━━━━\n"
    if not favs:
        text += "Favorite some books to build your Reading DNA!"
    else:
        fuids = [f["file_unique_id"] for f in favs][:500]
        files = await db.find_global("files", {"file_unique_id": {"$in": fuids}},
                                     proj={"genre": 1})
        counts = Counter((f.get("genre") or "Untagged") for f in files)
        if not counts:
            text += "Your favorites aren't genre-tagged yet — check back later."
        else:
            total = sum(counts.values())
            for genre, n in counts.most_common(8):
                pct = int(n / total * 100)
                bars = pct // 10
                text += f"{'🟪' * bars}{'⬜' * (10 - bars)} <b>{genre}</b> · {pct}%\n"
    await call.message.edit_text(text, reply_markup=kb([btn("🔙 Back", "acc_customize", style="danger")]))
