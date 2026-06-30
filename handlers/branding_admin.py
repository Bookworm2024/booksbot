"""
handlers/branding_admin.py — admin control for file branding (the renamer layer).

Admin → 🧰 More Tools → 🎨 Branding: set the cover image that's baked onto every
delivered file, set the channel handle appended to every caption
("Atomic Habits  @bookslibraryofficial"), and toggle the clean-name/cover prep.
Super-admin only. Backed by kv (utils.prepare reads these live).
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.connection import MongoManager
from utils import prepare
from utils.keyboards import btn, cancel_row, kb
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()


class BrandFSM(StatesGroup):
    img = State()
    handle = State()


async def _panel() -> tuple[str, object]:
    h = await prepare.handle()
    img_set = bool(await prepare.thumb_file_id())
    on = await prepare.brand_enabled()
    thumb_on = await prepare.thumb_enabled()
    text = (
        "🎨 <b>File Branding</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Every file the bot hands a reader is tidied and branded first: messy "
        "names become clean titles, the caption carries your handle, and your cover "
        "image is baked onto the file.</i>\n"
        "<blockquote>"
        f"⚙️ <b>Branding:</b> {'🟢 ON' if on else '🔴 OFF'}\n"
        f"🖼 <b>Cover image:</b> {'✅ set' if img_set else '— none yet'}  "
        f"(<b>{'on' if thumb_on else 'off'}</b>)\n"
        f"🏷 <b>Caption handle:</b> <code>{h or '— none —'}</code>"
        "</blockquote>\n"
        "<i>💡 The cover is added only to files the bot can re-upload (documents ≤20MB); "
        "every file still gets the clean title + handle caption.</i>"
    )
    rows = [
        [btn("🔴 Turn OFF" if on else "🟢 Turn ON", "brand_toggle",
             style="danger" if on else "success")],
        [btn("🖼 Set Cover Image", "brand_set_img", style="success"),
         btn("🏷 Set Handle", "brand_set_handle", style="primary")],
        [btn(("🖼 Cover: ON ✓" if thumb_on else "🖼 Cover: OFF"), "brand_thumb_toggle",
             style="primary")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return text, kb(*rows)


@router.callback_query(F.data == "admin_brand")
async def cb_brand(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "brand_toggle")
async def cb_toggle(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    db = await MongoManager.get()
    new = not await prepare.brand_enabled()
    await db.kv_set("brand_enabled", new)
    await call.answer("🟢 Branding ON." if new else "🔴 Branding paused.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "brand_thumb_toggle")
async def cb_thumb_toggle(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    db = await MongoManager.get()
    new = not await prepare.thumb_enabled()
    await db.kv_set("brand_thumb_enabled", new)
    await call.answer("🖼 Cover image ON." if new else "🖼 Cover image OFF.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "brand_set_img")
async def cb_set_img(call: CallbackQuery, state: FSMContext) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    await call.answer()
    await state.set_state(BrandFSM.img)
    await call.message.answer(
        "🖼 <b>Set Cover Image</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the image you want baked onto every delivered file (as a "
        "<b>photo</b>, or an image file). A square-ish cover works best — it's resized "
        "to a ≤320px thumbnail automatically.</blockquote>\n"
        "<i>💡 Tap Cancel to keep the current cover.</i>",
        reply_markup=kb(cancel_row("admin_brand")))


@router.message(BrandFSM.img, F.photo | F.document)
async def on_img(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.photo:
        fid = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        fid = message.document.file_id
    else:
        await message.answer("⚠️ <b>That wasn't an image.</b>\n<i>Send a photo or an image "
                             "file, or reopen 🎨 Branding to try again.</i>")
        return
    db = await MongoManager.get()
    await db.kv_set("brand_thumb_file_id", fid)
    prepare._thumb_cache.clear()   # bust the processed-thumbnail cache
    # validate it processes into a usable thumbnail
    ok = await prepare._thumb_bytes(message.bot)
    note = ("✅ It processes into a valid cover thumbnail."
            if ok else "⚠️ Saved, but it couldn't be processed into a thumbnail "
                       "(is Pillow installed? is the image valid?). Captions still brand fine.")
    text, markup = await _panel()
    await message.answer(f"🖼 <b>Cover image saved.</b>\n<i>{note}</i>")
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "brand_set_handle")
async def cb_set_handle(call: CallbackQuery, state: FSMContext) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    await call.answer()
    await state.set_state(BrandFSM.handle)
    await call.message.answer(
        "🏷 <b>Set Caption Handle</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the handle to append to every file caption, e.g. "
        "<code>@bookslibraryofficial</code>. Send <code>-</code> to remove it.</blockquote>\n"
        "<i>💡 Tap Cancel to keep the current handle.</i>",
        reply_markup=kb(cancel_row("admin_brand")))


@router.message(BrandFSM.handle, F.text)
async def on_handle(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ <b>Unchanged.</b>")
        return
    await state.clear()
    handle = "" if raw in ("-", "—") else raw
    db = await MongoManager.get()
    await db.kv_set("brand_handle", handle)
    text, markup = await _panel()
    await message.answer("🏷 <b>Handle updated.</b>")
    await message.answer(text, reply_markup=markup)


@router.message(Command("branding"))
async def cmd_branding(message: Message) -> None:
    if not is_super(message.chat.id):
        return
    text, markup = await _panel()
    await message.answer(text, reply_markup=markup)
