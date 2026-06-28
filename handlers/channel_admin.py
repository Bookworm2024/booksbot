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
from utils.permissions import has

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
    cur_s = f"<code>{cur}</code>" if cur else "<i>not yet connected</i>"
    return (
        "🗂 <b>File &amp; Database Channel</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>The vault behind your library — every download flows through here.</i>\n\n"
        f"🔗 <b>Connected channel</b> · {cur_s}\n\n"
        "<blockquote>Every search result, paid delivery and saved favourite is "
        "served from this channel, so the bot must sit inside it as a "
        "<b>member or admin</b>. Keep it healthy and the whole archive stays "
        "fast and reliable.</blockquote>\n\n"
        "<blockquote>✏️ <b>Change Channel ID</b> — point the bot at a new archive. "
        "Send its chat id (e.g. <code>-1001234567890</code>) or simply forward any "
        "message from it.\n"
        "📥 <b>Import Old Files</b> — bring legacy uploads into the index so they "
        "become searchable and deliverable. Fresh uploads index themselves "
        "automatically.\n"
        "🔎 <b>Diagnostics</b> — a quick health check on access and coverage.</blockquote>\n\n"
        "<i>💡 New here? Connect the channel first, then run Diagnostics to confirm "
        "the bot can reach it.</i>"
    )


@router.callback_query(F.data == "admin_filechan")
async def cb_filechan(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("👑 This tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        await _panel_text(),
        reply_markup=kb([btn("✏️ Change Channel ID", "filechan_change", style="primary")],
                        [btn("📥 Import Old Files", "admin_import", style="success")],
                        [btn("🔎 Run Diagnostics", "filechan_diag", style="primary")],
                        [btn("🔙 Back to Admin", "admin_open", style="danger")]))


@router.callback_query(F.data == "filechan_diag")
async def cb_diag(call: CallbackQuery) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("👑 This tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer("Running a quick health check…")
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
                access = f"✅ <b>{title}</b> — bot is <i>{role}</i>"
            except Exception:  # noqa: BLE001
                access = f"✅ Reachable — <b>{title}</b>"
        except Exception as exc:  # noqa: BLE001
            access = ("❌ Out of reach — add the bot as an <b>admin</b> in the channel\n"
                      f"<i>{str(exc)[:80]}</i>")

    health = "🟢 Healthy &amp; serving" if (live and total > 0) else "🔴 Setup needed"
    tips = []
    if not live:
        tips.append("✏️ <b>Change Channel ID</b> — connect your archive channel")
    if total == 0:
        tips.append("📥 <b>Import Old Files</b> — index existing uploads (or run the Telethon backfill)")
    tip_block = ("\n\n<b>Next steps to go live:</b>\n<blockquote>" + "\n".join(tips) + "</blockquote>") if tips else ""

    await call.message.edit_text(
        "🔎 <b>Archive Diagnostics</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>A live snapshot of your library's plumbing.</i>\n\n"
        f"<blockquote>📊 <b>Status</b> · {health}\n"
        f"📚 <b>Files indexed</b> · <code>{total}</code>\n"
        f"📤 <b>Ready to deliver</b> · <code>{deliverable}</code> <i>(have a message id)</i>\n"
        f"🗂 <b>Channel</b> · <code>{live or 'not set'}</code>\n"
        f"🔌 <b>Access</b> · {access}</blockquote>"
        + tip_block,
        reply_markup=kb([btn("🔄 Re-run Check", "filechan_diag", style="primary")],
                        [btn("🔙 Back", "admin_filechan", style="primary")]))


# ── change channel id ────────────────────────────────────────────────────────
@router.callback_query(F.data == "filechan_change")
async def cb_change(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_super(call.from_user.id):
        await call.answer("👑 This tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ChannelFSM.awaiting_channel_id)
    await call.message.answer(
        "🗂 <b>Connect Your File Channel</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Two easy ways — pick whichever is quicker.</i>\n\n"
        "<blockquote>🔢 <b>Send the chat id</b> — paste the channel's id, e.g. "
        "<code>-1001234567890</code>.\n"
        "↪️ <b>Forward a message</b> — forward anything from the channel and I'll "
        "read its id for you.</blockquote>\n\n"
        "<i>💡 Channel ids are negative and usually begin with <code>-100</code>.</i>\n\n"
        "Send <code>/cancel</code> anytime to step away.")


@router.message(ChannelFSM.awaiting_channel_id)
async def on_channel_id(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    # let any slash-command (other than the forward case below) break out of the
    # flow instead of being black-holed by this state handler
    if txt.startswith("/") and getattr(message, "forward_origin", None) is None:
        await state.clear()
        if txt.lower() != "/cancel":
            await message.answer("↩️ <b>Channel setup paused.</b>\n<i>Nothing changed — reopen the panel whenever you're ready.</i>")
        else:
            await message.answer("✅ <b>Setup cancelled.</b>\n<i>Your file channel stays exactly as it was.</i>")
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
            await message.answer("⚠️ <b>That doesn't look like a chat id.</b>\n"
                                 "<i>Send a numeric id such as <code>-1001234567890</code>, "
                                 "or forward any message from the channel and I'll read it for you.</i>")
            return
        # channels/supergroups are large negative ids; reject obvious mistakes
        if new_id >= 0:
            await message.answer("⚠️ <b>That id looks off.</b>\n"
                                 "<i>A channel id is negative and usually starts with "
                                 "<code>-100</code>. Double-check it and resend.</i>")
            return

    await state.clear()
    await set_file_channel(new_id)
    await log_action(message.chat.id, "set_file_channel", str(new_id))

    # best-effort access check (the bot must be a member to index/deliver)
    note = ""
    try:
        chat = await message.bot.get_chat(new_id)
        title = getattr(chat, "title", None) or new_id
        note = (f"\n\n<blockquote>🔗 <b>Linked to</b> · {title}\n"
                "✅ The bot can reach it — new uploads will index automatically.</blockquote>")
    except Exception:  # noqa: BLE001
        note = ("\n\n<blockquote>⚠️ <b>One quick step left.</b>\n"
                "I can't read that channel yet. Add the bot as an <b>admin</b> there, and "
                "every new upload will index itself from then on.</blockquote>")

    await message.answer(
        "✨ <b>File channel connected</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🗂 <b>Channel</b> · <code>{new_id}</code>"
        f"{note}\n\n"
        "<i>💡 Have older uploads from before the bot joined? Import them next so they "
        "show up in search.</i>",
        reply_markup=kb([btn("📥 Import Old Files", "admin_import", style="success")],
                        [btn("🔙 Back to Admin", "admin_open", style="primary")]))


# ── import old files (forward-to-index) ──────────────────────────────────────
@router.callback_query(F.data == "admin_import")
async def cb_import(call: CallbackQuery, state: FSMContext) -> None:
    if not await has(call.from_user.id, "content"):
        await call.answer("🔒 You don't have permission for this — ask the owner to enable it.", show_alert=True)
        return
    live = await get_file_channel()
    if not live:
        await call.answer("Connect your file channel first, then come back to import.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ChannelFSM.importing)
    await state.update_data(imported=0, skipped=0)
    await call.message.answer(
        "📥 <b>Import Old Files</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Let's bring your legacy uploads into the library.</i>\n\n"
        "<blockquote>📤 <b>Forward the old files</b> straight from the channel — as "
        "many in a row as you like.\n"
        "📚 I'll index each one so it becomes <b>searchable</b> and ready to "
        "<b>deliver</b> on demand.\n"
        "🔁 Already-indexed files are skipped automatically, so re-forwarding is "
        "perfectly safe.</blockquote>\n\n"
        "Send <code>/done</code> when you've finished.")


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
            "✨ <b>Import complete</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Your archive just grew. Nicely done.</i>\n\n"
            f"<blockquote>📚 <b>Newly indexed</b> · <code>{data.get('imported', 0)}</code>\n"
            f"⏭ <b>Already on the shelf</b> · <code>{data.get('skipped', 0)}</code></blockquote>\n\n"
            "<i>💡 The new titles are searchable right away — try a search to see them land.</i>",
            reply_markup=kb([btn("🗂 File Channel", "admin_filechan", style="primary")],
                            [btn("🔙 Back to Admin", "admin_open", style="primary")]))
        return

    data = await state.get_data()
    imported = int(data.get("imported", 0))
    skipped = int(data.get("skipped", 0))

    fchat, fmsg_id = _forward_channel(message)
    if fchat is None or fmsg_id is None:
        await message.answer("⏭ <b>That wasn't a forward from a channel.</b>\n"
                             "<i>Forward the file straight from the channel — forward-privacy "
                             "can hide the source, in which case the original sender's id is lost.</i>")
        return

    live = await get_file_channel()
    if live and fchat != live:
        await message.answer(
            "⏭ <b>Skipped — wrong channel.</b>\n"
            f"<i>That file came from <code>{fchat}</code>, not your connected file "
            f"channel <code>{live}</code>. Forward it from the right channel and I'll index it.</i>")
        return

    item = extract_from_message(message, msg_id=fmsg_id, chan_id=fchat)
    if not item:
        skipped += 1
        await state.update_data(skipped=skipped)
        await message.answer("⏭ <b>No file in that message.</b>\n"
                             f"<i>Nothing to index here — running tally: indexed <code>{imported}</code>, "
                             f"skipped <code>{skipped}</code>.</i>")
        return

    created = await index_file(item)
    if created:
        imported += 1
        await state.update_data(imported=imported)
        await message.answer(f"📚 <b>Indexed</b> · {item['name'][:60]}\n"
                             f"<i>On the shelf — total this session: <code>{imported}</code>.</i>")
    else:
        skipped += 1
        await state.update_data(skipped=skipped)
        await message.answer(f"⏭ <b>Already in your library</b> · {item['name'][:60]}\n"
                             f"<i>Skipped to avoid a duplicate — skipped so far: <code>{skipped}</code>.</i>")
