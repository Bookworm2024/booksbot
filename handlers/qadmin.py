"""
handlers/qadmin.py — admin question bank management for the Mini-App games.

Super-admin panel → 🎮 Questions:
  • Add Quiz   — level → question → 4 options → correct
  • Add T/F    — question → True/False
  • Counts     — bank size per game/level
Questions feed utils.games (the seeded starter bank is supplemented by these).
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, cancel_row, kb
from utils.permissions import has

logger = logging.getLogger(__name__)
router = Router()


class QAdminFSM(StatesGroup):
    quiz_q = State()
    quiz_a = State()
    quiz_b = State()
    quiz_c = State()
    quiz_d = State()
    tf_q = State()


def _menu():
    return kb([btn("➕ Compose a Quiz Question", "qa_addquiz", style="success")],
              [btn("➕ Compose a True / False", "qa_addtf", style="success")],
              [btn("📊 Bank Overview", "qa_counts", style="primary")],
              [btn("🔙 Back to Admin", "admin_open", style="danger")])


@router.callback_query(F.data == "admin_qbank")
async def cb_qbank(call: CallbackQuery) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🎮 <b>Question Bank</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>The trivia engine behind every quiz round — curated by you.</i>\n\n"
        "<blockquote>Every question you add here flows straight into the games "
        "players love. Build the <b>Quiz</b> bank across three difficulty tiers, "
        "or drop in quick <b>True / False</b> rounds — all server-scored and "
        "rewarded in 💎 BGM.</blockquote>\n\n"
        "<i>💡 Choose a tool below to compose a new question or review the bank.</i>",
        reply_markup=_menu())


@router.callback_query(F.data == "qa_counts")
async def cb_counts(call: CallbackQuery) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    db = await MongoManager.get()
    lines = ["📊 <b>Bank Overview</b>",
             "━━━━━━━━━━━━━━━━━━━━",
             "<i>A live count of every question feeding the games.</i>\n",
             "<blockquote>"]
    for lvl in ("beginner", "moderate", "advanced"):
        n = await db.count_global("questions", {"game": "quiz", "level": lvl})
        lines.append(f"🧠 <b>Quiz</b> · {lvl} — <code>{n}</code>")
    tf = await db.count_global("questions", {"game": "tf"})
    lines.append(f"✅ <b>True / False</b> — <code>{tf}</code>")
    lines.append("</blockquote>")
    lines.append("\n<i>💡 A deeper bank keeps rounds fresh — add a few more to "
                 "round out the thinner tiers.</i>")
    await call.message.edit_text("\n".join(lines), reply_markup=_menu())


# ── add quiz ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "qa_addquiz")
async def cb_addquiz(call: CallbackQuery) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🧠 <b>Compose a Quiz Question</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Four options, one right answer — pick the tier it belongs in.</i>\n\n"
        "<blockquote>🟢 <b>Beginner</b> — light, welcoming, gets new players "
        "winning.\n"
        "🟡 <b>Moderate</b> — a satisfying think for regular readers.\n"
        "🔴 <b>Advanced</b> — for the well-read who want a real challenge.</blockquote>\n\n"
        "<i>💡 Choose a difficulty to begin.</i>",
        reply_markup=kb([btn("🟢 Beginner", "qa_lvl:beginner", style="success"),
                         btn("🟡 Moderate", "qa_lvl:moderate", style="primary"),
                         btn("🔴 Advanced", "qa_lvl:advanced", style="danger")],
                        [btn("🔙 Back", "admin_qbank", style="danger")]))


@router.callback_query(F.data.startswith("qa_lvl:"))
async def cb_level(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    await state.update_data(level=call.data.split(":", 1)[1])
    await state.set_state(QAdminFSM.quiz_q)
    await call.message.answer(
        "✍️ <b>The Question</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Type the question exactly as players should see it. Keep it "
        "clear and self-contained — no need to number it.</blockquote>\n\n"
        "<i>💡 Tap Cancel below to step away.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(QAdminFSM.quiz_q, F.text)
async def q_question(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ <b>Cancelled</b>\n"
            "<i>No changes were saved — your bank is untouched. Start again any "
            "time from the Question Bank.</i>")
        return
    await state.update_data(q=message.text.strip())
    await state.set_state(QAdminFSM.quiz_a)
    await message.answer(
        "🅰️ <b>Option A</b>\n"
        "<i>Send the first answer choice.</i>")


@router.message(QAdminFSM.quiz_a, F.text)
async def q_a(message: Message, state: FSMContext) -> None:
    await state.update_data(A=message.text.strip())
    await state.set_state(QAdminFSM.quiz_b)
    await message.answer(
        "🅱️ <b>Option B</b>\n"
        "<i>Send the second answer choice.</i>")


@router.message(QAdminFSM.quiz_b, F.text)
async def q_b(message: Message, state: FSMContext) -> None:
    await state.update_data(B=message.text.strip())
    await state.set_state(QAdminFSM.quiz_c)
    await message.answer(
        "🇨 <b>Option C</b>\n"
        "<i>Send the third answer choice.</i>")


@router.message(QAdminFSM.quiz_c, F.text)
async def q_c(message: Message, state: FSMContext) -> None:
    await state.update_data(C=message.text.strip())
    await state.set_state(QAdminFSM.quiz_d)
    await message.answer(
        "🇩 <b>Option D</b>\n"
        "<i>Send the fourth and final answer choice.</i>")


@router.message(QAdminFSM.quiz_d, F.text)
async def q_d(message: Message, state: FSMContext) -> None:
    await state.update_data(D=message.text.strip())
    data = await state.get_data()
    await message.answer(
        "🎯 <b>Mark the Correct Answer</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Tap the option players should be rewarded for.</i>\n\n"
        f"<blockquote>🅰️ {data['A']}\n🅱️ {data['B']}\n🇨 {data['C']}\n🇩 {data['D']}</blockquote>",
        reply_markup=kb([btn("A", "qa_correct:A", style="primary"),
                         btn("B", "qa_correct:B", style="primary"),
                         btn("C", "qa_correct:C", style="primary"),
                         btn("D", "qa_correct:D", style="primary")]))


@router.callback_query(F.data.startswith("qa_correct:"))
async def cb_correct(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    ans = call.data.split(":", 1)[1]
    data = await state.get_data()
    await state.clear()
    db = await MongoManager.get()
    await db.safe_insert("questions", {
        "game": "quiz", "level": data.get("level", "beginner"),
        "q": data.get("q"), "options": {k: data.get(k) for k in "ABCD"}, "a": ans,
    })
    await call.answer("Saved — it's now live in the quiz bank.")
    await call.message.edit_text(
        "✅ <b>Quiz Question Added</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Your question is in the bank and will start appearing in "
        "rounds straight away. Players who get it right earn 💎 BGM.</blockquote>\n\n"
        "<i>💡 Add another to keep the rotation fresh, or review the full bank below.</i>",
        reply_markup=_menu())


# ── add true/false ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "qa_addtf")
async def cb_addtf(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    await call.answer()
    await state.set_state(QAdminFSM.tf_q)
    await call.message.answer(
        "✅ <b>Compose a True / False</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Type a single statement players will judge as true or "
        "false. Make it unambiguous — there should be one clear correct "
        "verdict.</blockquote>\n\n"
        "<i>💡 Tap Cancel below to step away.</i>",
        reply_markup=kb(cancel_row("admin_open")))


@router.message(QAdminFSM.tf_q, F.text)
async def tf_question(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer(
            "❌ <b>Cancelled</b>\n"
            "<i>No changes were saved — your bank is untouched. Start again any "
            "time from the Question Bank.</i>")
        return
    await state.update_data(q=message.text.strip())
    await message.answer(
        "🎯 <b>Set the Verdict</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Is your statement true or false? Tap the correct answer.</i>",
        reply_markup=kb([btn("✅ True", "qa_tf:1", style="success"),
                         btn("❌ False", "qa_tf:0", style="danger")]))


@router.callback_query(F.data.startswith("qa_tf:"))
async def cb_tf_answer(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    val = call.data.split(":", 1)[1] == "1"
    data = await state.get_data()
    await state.clear()
    db = await MongoManager.get()
    await db.safe_insert("questions", {"game": "tf", "q": data.get("q"), "a": val})
    await call.answer("Saved — it's now live in the True / False bank.")
    await call.message.edit_text(
        "✅ <b>True / False Added</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Your statement is in the bank and ready to appear in "
        "rounds. A correct verdict earns players 💎 BGM.</blockquote>\n\n"
        "<i>💡 Add another in seconds, or review the full bank below.</i>",
        reply_markup=_menu())
