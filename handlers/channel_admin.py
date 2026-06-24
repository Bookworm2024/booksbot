"""
handlers/channel_admin.py — manage the file/database channel from the admin panel.

Two super-admin tools (Admin panel → 🗂 File Channel):

  ✏️ Change Channel ID — repoint the bot at a new archive channel by sending its
     chat id (or forwarding any message from it). Stored live in Mongo `kv`
     (utils.channel) — takes effect instantly, no redeploy. The indexer, paid
     download delivery and favorite re-delivery all read this live id.

  📥 Import Old Files — the Bot API can't read a channel's history, so files that
     were already in the channel before the bot joined are invisible to the live
     indexer. Here the admin FORWARDS those old files to the bot; each forward
     carries `forward_origin` (the source channel + the ORIGINAL message id), so
     we index them with the right msg_id for copy_message delivery — and, because
     the file physically passed through the bot, with a bot-usable file_id too.
     (For the bulk ~30k history, tools/backfill.py uses a Telethon userbot.)
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from database.connection import MongoManager
from utils.audit import log_action
from utils.channel import get_file_channel, set_file_channel
from utils.files import archive_count, extract_from_message, index_file
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()


class ChannelFSM(StatesGroup):
    awaiting_channel_id = State()
    importing = State()


def _is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


def _forward_channel(message: Message) -> tuple[int | None, int | None]:
    """If `message` was forwarded from a channel, return (chat_id, original_msg_id),
    else (None, None). Handles aiogram 3.x `forward_origin` (MessageOriginChannel)
    and the legacy `forward_from_chat`/`forward_from_message_id` fields."""
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        mid = getattr(origin, "message_id", None)
        if chat is not None and mid is not None:
            return chat.id, mid
    fchat = getattr(message, "forward_from_chat", None)
    fmid = getattr(message, "forward_from_message_id", None)
    if fchat is not None and fmid is not None:
        return fchat.id, fmid
    return None, None


# ── panel ────────────────────────────────────────────────────────────────────
async def _panel_text() -> str:
    cur = await get_file_channel()
    cur_s = f"<code>{cur}</code>" if cur else "<i>not set</i>"
    return (
        "🗂 <b>File / Database Channel</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Current: {cur_s}\n\n"
        "All file delivery and indexing route through this channel. The bot must "
        "be a <b>member/admin</b> there.\n\n"
        "• <b>Change Channel ID</b> — send the new chat id (e.g. "
        "<code>-1001234567890</code>) or forward any message from the channel.\n"
        "• <b>Import Old Files</b> — forward existing files so they get indexed "
        "(new uploads are indexed automatically)."
    )


@router.callback_query(F.data == "admin_filechan")
async def cb_filechan(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _panel_text(),
        reply_markup=kb([btn("✏️ Change Channel ID", "filechan_change", style="primary")],
                        [btn("📥 Import Old Files", "admin_import", style="success")],
                        [btn("🔎 Diagnostics", "filechan_diag", style="primary")],
                        [btn("🔙 Back", "admin_open", style="danger")]))


@router.callback_query(F.data == "filechan_diag")
async def cb_diag(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer("Checking…")
    db = await MongoManager.get()
    total = await archive_count()
    deliverable = await db.count_global("files", {"msg_id": {"$ne": None}})
    live = await get_file_channel()

    # can the bot actually reach the channel (needed to index new posts & deliver)?
    access = "—"
    if live:
        try:
            chat = await call.bot.get_chat(live)
            title = getattr(chat, "title", None) or live
            try:
                me = await call.bot.get_chat_member(live, (await call.bot.get_me()).id)
                role = getattr(me, "status", "member")
                access = f"✅ <b>{title}</b> (bot is {role})"
            except Exception:  # noqa: BLE001
                access = f"✅ reachable: <b>{title}</b>"
        except Exception as exc:  # noqa: BLE001
            access = f"❌ can't reach it — add the bot as <b>admin</b>\n<i>{str(exc)[:80]}</i>"

    health = "🟢 healthy" if (live and total > 0) else "🔴 needs setup"
    tips = []
    if not live:
        tips.append("• Set the channel: <b>✏️ Change Channel ID</b>")
    if total == 0:
        tips.append("• Import existing files: <b>📥 Import Old Files</b> (or run the Telethon backfill)")
    tip_block = ("\n\n<b>To fix:</b>\n" + "\n".join(tips)) if tips else ""

    await call.message.edit_text(
        "🔎 <b>Archive Diagnostics</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Status: <b>{health}</b>\n"
        f"📚 Files indexed: <b>{total}</b>\n"
        f"📤 Deliverable (have msg id): <b>{deliverable}</b>\n"
        f"🗂 Channel: <code>{live or 'not set'}</code>\n"
        f"🔌 Access: {access}"
        + tip_block,
        reply_markup=kb([btn("🔄 Refresh", "filechan_diag", style="primary")],
                        [btn("🔙 Back", "admin_filechan", style="primary")]))


# ── change channel id ────────────────────────────────────────────────────────
@router.callback_query(F.data == "filechan_change")
async def cb_change(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    await state.set_state(ChannelFSM.awaiting_channel_id)
    await call.message.answer(
        "🗂 <b>Set File Channel</b>\n\nSend the channel's <b>chat id</b> "
        "(e.g. <code>-1001234567890</code>), or <b>forward any message</b> from the "
        "channel and I'll read its id.\n\n/cancel to abort.")


@router.message(ChannelFSM.awaiting_channel_id)
async def on_channel_id(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    # let any slash-command (other than the forward case below) break out of the
    # flow instead of being black-holed by this state handler
    if txt.startswith("/") and getattr(message, "forward_origin", None) is None:
        await state.clear()
        if txt.lower() != "/cancel":
            await message.answer("↩️ Left the channel setup. Run the command again.")
        else:
            await message.answer("❌ Cancelled.")
        return

    # 1) a forwarded message reveals the channel id directly
    fchat, _ = _forward_channel(message)
    if fchat is not None:
        new_id = int(fchat)
    else:
        raw = (message.text or "").strip()
        try:
            new_id = int(raw)
        except (TypeError, ValueError):
            await message.answer("⚠️ Send a numeric chat id like <code>-1001234567890</code>, "
                                 "or forward a message from the channel.")
            return
        # channels/supergroups are large negative ids; reject obvious mistakes
        if new_id >= 0:
            await message.answer("⚠️ A channel id is negative and usually starts with "
                                 "<code>-100</code>. Double-check and resend.")
            return

    await state.clear()
    await set_file_channel(new_id)
    await log_action(message.chat.id, "set_file_channel", str(new_id))

    # best-effort access check (the bot must be a member to index/deliver)
    note = ""
    try:
        chat = await message.bot.get_chat(new_id)
        title = getattr(chat, "title", None) or new_id
        note = f"\n✅ Connected to <b>{title}</b>."
    except Exception:  # noqa: BLE001
        note = ("\n⚠️ I couldn't read that chat yet — add me as an <b>admin</b> in the "
                "channel, then new uploads will index automatically.")

    await message.answer(
        f"✅ File channel set to <code>{new_id}</code>.{note}",
        reply_markup=kb([btn("📥 Import Old Files", "admin_import", style="success")],
                        [btn("🔙 Admin", "admin_open", style="primary")]))


# ── import old files (forward-to-index) ──────────────────────────────────────
@router.callback_query(F.data == "admin_import")
async def cb_import(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    live = await get_file_channel()
    if not live:
        await call.answer("Set the file channel first.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ChannelFSM.importing)
    await state.update_data(imported=0, skipped=0)
    await call.message.answer(
        "📥 <b>Import Old Files</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Forward me the old files from the channel (you can forward many in a row). "
        "I'll index each so it becomes searchable and deliverable.\n\n"
        "Send <b>/done</b> when finished.")


@router.message(ChannelFSM.importing)
async def on_import(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    # /done, /cancel, or any other slash-command exits the import flow (but a
    # forwarded file with a caption starting "/" is still a file, so require no
    # forward_origin before treating it as a command).
    if text.startswith("/") and getattr(message, "forward_origin", None) is None:
        data = await state.get_data()
        await state.clear()
        await message.answer(
            f"✅ <b>Import finished.</b>\n📚 Indexed: <b>{data.get('imported', 0)}</b> · "
            f"⏭ Skipped: <b>{data.get('skipped', 0)}</b>",
            reply_markup=kb([btn("🗂 File Channel", "admin_filechan", style="primary")],
                            [btn("🔙 Admin", "admin_open", style="primary")]))
        return

    data = await state.get_data()
    imported = int(data.get("imported", 0))
    skipped = int(data.get("skipped", 0))

    fchat, fmsg_id = _forward_channel(message)
    if fchat is None or fmsg_id is None:
        await message.answer("⏭ That isn't a forward from a channel — forward the file "
                             "<i>from the channel</i> (forward-privacy can hide the source).")
        return

    live = await get_file_channel()
    if live and fchat != live:
        await message.answer(
            f"⏭ Skipped — that file came from <code>{fchat}</code>, not the configured "
            f"file channel <code>{live}</code>.")
        return

    item = extract_from_message(message, msg_id=fmsg_id, chan_id=fchat)
    if not item:
        skipped += 1
        await state.update_data(skipped=skipped)
        await message.answer(f"⏭ No file found in that message. (Indexed {imported}, skipped {skipped}.)")
        return

    created = await index_file(item)
    if created:
        imported += 1
        await state.update_data(imported=imported)
        await message.answer(f"📚 Indexed <b>{item['name'][:60]}</b>  ·  total {imported}")
    else:
        skipped += 1
        await state.update_data(skipped=skipped)
        await message.answer(f"⏭ Already indexed: <b>{item['name'][:60]}</b>  ·  skipped {skipped}")
