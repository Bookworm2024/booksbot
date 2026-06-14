"""
handlers/gift.py — gift BGM to a friend.

Account → 🎁 Gift BGM → recipient user-id → amount → confirm → atomic transfer
(deduct from sender only if they hold enough; credit recipient). Recipient must
have started the bot. Viral + engagement driver.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils.keyboards import btn, kb
from utils.wallet import add_bgm, get_balances

logger = logging.getLogger(__name__)
router = Router()

_MIN_GIFT = 1.0


class GiftFSM(StatesGroup):
    awaiting_recipient = State()
    awaiting_amount = State()


@router.message(Command("gift"))
async def cmd_gift(message: Message, state: FSMContext) -> None:
    await _open(message, state)


@router.callback_query(F.data == "acc_gift")
async def cb_gift(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _open(call.message, state)


async def _open(message: Message, state: FSMContext) -> None:
    bgm, _ = await get_balances(message.chat.id)
    await state.set_state(GiftFSM.awaiting_recipient)
    await message.answer(
        "🎁 <b>Gift BGM</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Your balance: <b>{bgm:.2f} BGM</b>\n\n"
        "Send the <b>recipient's User ID</b> (they must have started the bot).\n"
        "/cancel to abort.")


@router.message(GiftFSM.awaiting_recipient, F.text)
async def on_recipient(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    if not raw.isdigit():
        await message.answer("⚠️ Send a numeric User ID.")
        return
    target = int(raw)
    if target == message.chat.id:
        await message.answer("😅 You can't gift yourself.")
        return
    db = await MongoManager.get()
    if not await db.find_one_global("users", {"user_id": target}, {"_id": 1}):
        await message.answer("❌ That user hasn't started the bot yet.")
        return
    await state.update_data(target=target)
    await state.set_state(GiftFSM.awaiting_amount)
    await message.answer(f"💎 How much <b>BGM</b> to gift to <code>{target}</code>? "
                         f"(min {_MIN_GIFT:g})")


@router.message(GiftFSM.awaiting_amount, F.text)
async def on_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Cancelled.")
        return
    try:
        amount = round(float(raw), 3)
    except ValueError:
        await message.answer("⚠️ Enter a number.")
        return
    if amount < _MIN_GIFT:
        await message.answer(f"⚠️ Minimum gift is {_MIN_GIFT:g} BGM.")
        return
    data = await state.get_data()
    target = data.get("target")
    await state.clear()
    sender = message.chat.id

    # atomic: deduct from sender only if they still hold enough
    db = await MongoManager.get()
    debited = await db.find_one_and_update_global(
        "users", {"user_id": sender, "bookgem": {"$gte": amount}},
        {"$inc": {"bookgem": -amount}})
    if not debited:
        await message.answer("❌ Insufficient BGM balance.")
        return
    await add_bgm(target, amount)
    await message.answer(f"✅ Sent <b>{amount:g} BGM</b> to <code>{target}</code>. 🎁",
                         reply_markup=kb([btn("💼 Balance", "acc_balance", style="primary")]))
    try:
        await message.bot.send_message(
            target, f"🎁 <b>You received a gift!</b>\n💎 <b>+{amount:g} BGM</b> "
            f"from <code>{sender}</code>.")
    except Exception:  # noqa: BLE001
        pass
