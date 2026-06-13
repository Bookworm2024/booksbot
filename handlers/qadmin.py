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

from config import ADMIN_IDS
from database.connection import MongoManager
from utils.keyboards import btn, kb

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
    return kb([btn("➕ Add Quiz", "qa_addquiz", style="success")],
              [btn("➕ Add True/False", "qa_addtf", style="success")],
              [btn("📊 Counts", "qa_counts", style="primary")],
              [btn("🔙 Back", "admin_open", style="danger")])


@router.callback_query(F.data == "admin_qbank")
async def cb_qbank(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("🎮 <b>Question Bank</b>\n\nManage Quiz & True/False questions.",
                                 reply_markup=_menu())


@router.callback_query(F.data == "qa_counts")
async def cb_counts(call: CallbackQuery) -> None:
    await call.answer()
    db = await MongoManager.get()
    lines = ["📊 <b>Question Bank</b>\n"]
    for lvl in ("beginner", "moderate", "advanced"):
        n = await db.count_global("questions", {"game": "quiz", "level": lvl})
        lines.append(f"🧠 Quiz · {lvl}: <b>{n}</b>")
    tf = await db.count_global("questions", {"game": "tf"})
    lines.append(f"✅ True/False: <b>{tf}</b>")
    await call.message.edit_text("\n".join(lines), reply_markup=_menu())


# ── add quiz ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "qa_addquiz")
async def cb_addquiz(call: CallbackQuery) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🧠 <b>Add Quiz Question</b>\n\nChoose the level:",
        reply_markup=kb([btn("🟢 Beginner", "qa_lvl:beginner", style="success"),
                         btn("🟡 Moderate", "qa_lvl:moderate", style="primary"),
                         btn("🔴 Advanced", "qa_lvl:advanced", style="danger")],
                        [btn("🔙 Back", "admin_qbank", style="danger")]))


@router.callback_query(F.data.startswith("qa_lvl:"))
async def cb_level(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.update_data(level=call.data.split(":", 1)[1])
    await state.set_state(QAdminFSM.quiz_q)
    await call.message.answer("✍️ Send the <b>question text</b> (/cancel to abort):")


@router.message(QAdminFSM.quiz_q, F.text)
async def q_question(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.update_data(q=message.text.strip())
    await state.set_state(QAdminFSM.quiz_a)
    await message.answer("Option <b>A</b>:")


@router.message(QAdminFSM.quiz_a, F.text)
async def q_a(message: Message, state: FSMContext) -> None:
    await state.update_data(A=message.text.strip())
    await state.set_state(QAdminFSM.quiz_b)
    await message.answer("Option <b>B</b>:")


@router.message(QAdminFSM.quiz_b, F.text)
async def q_b(message: Message, state: FSMContext) -> None:
    await state.update_data(B=message.text.strip())
    await state.set_state(QAdminFSM.quiz_c)
    await message.answer("Option <b>C</b>:")


@router.message(QAdminFSM.quiz_c, F.text)
async def q_c(message: Message, state: FSMContext) -> None:
    await state.update_data(C=message.text.strip())
    await state.set_state(QAdminFSM.quiz_d)
    await message.answer("Option <b>D</b>:")


@router.message(QAdminFSM.quiz_d, F.text)
async def q_d(message: Message, state: FSMContext) -> None:
    await state.update_data(D=message.text.strip())
    data = await state.get_data()
    await message.answer(
        f"Which is correct?\n\nA. {data['A']}\nB. {data['B']}\nC. {data['C']}\nD. {data['D']}",
        reply_markup=kb([btn("A", "qa_correct:A", style="primary"),
                         btn("B", "qa_correct:B", style="primary"),
                         btn("C", "qa_correct:C", style="primary"),
                         btn("D", "qa_correct:D", style="primary")]))


@router.callback_query(F.data.startswith("qa_correct:"))
async def cb_correct(call: CallbackQuery, state: FSMContext) -> None:
    ans = call.data.split(":", 1)[1]
    data = await state.get_data()
    await state.clear()
    db = await MongoManager.get()
    await db.safe_insert("questions", {
        "game": "quiz", "level": data.get("level", "beginner"),
        "q": data.get("q"), "options": {k: data.get(k) for k in "ABCD"}, "a": ans,
    })
    await call.answer("Saved ✅")
    await call.message.edit_text("✅ Quiz question added.", reply_markup=_menu())


# ── add true/false ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "qa_addtf")
async def cb_addtf(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Access denied", show_alert=True)
        return
    await call.answer()
    await state.set_state(QAdminFSM.tf_q)
    await call.message.answer("✅ <b>Add True/False</b>\n\nSend the statement (/cancel to abort):")


@router.message(QAdminFSM.tf_q, F.text)
async def tf_question(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() == "/cancel":
        await state.clear(); await message.answer("❌ Cancelled."); return
    await state.update_data(q=message.text.strip())
    await message.answer("Is it TRUE or FALSE?",
                         reply_markup=kb([btn("✅ True", "qa_tf:1", style="success"),
                                          btn("❌ False", "qa_tf:0", style="danger")]))


@router.callback_query(F.data.startswith("qa_tf:"))
async def cb_tf_answer(call: CallbackQuery, state: FSMContext) -> None:
    val = call.data.split(":", 1)[1] == "1"
    data = await state.get_data()
    await state.clear()
    db = await MongoManager.get()
    await db.safe_insert("questions", {"game": "tf", "q": data.get("q"), "a": val})
    await call.answer("Saved ✅")
    await call.message.edit_text("✅ True/False question added.", reply_markup=_menu())
