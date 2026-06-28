"""
handlers/favorites.py — personal saved-files collection.

  ⭐ Add to Favorites (from a delivered file or search result)
  📖 My Library → Favorites → paginated list → view (free re-delivery) / remove

Re-delivering a favorite is FREE (the user already paid once), routed through
copy_message from the archive channel like the paid path.
"""
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import BOT_PUBLIC_URL
from utils.channel import get_file_channel
from database.connection import MongoManager
from utils.files import get_file, icon_for
from utils.keyboards import btn, kb, webapp_btn

_AUDIO_EXT = {"mp3", "m4b", "m4a", "wav", "ogg", "flac", "aac"}

logger = logging.getLogger(__name__)
router = Router()

_PER_PAGE = 6


@router.callback_query(F.data.startswith("fav_add:"))
async def cb_fav_add(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    f = await get_file(fuid)
    if not f:
        await call.answer("⚠️ We couldn't find that file. It may have been moved — try searching for it again.", show_alert=True)
        return
    db = await MongoManager.get()
    if await db.find_one_global("favorites", {"user_id": uid, "file_unique_id": fuid}):
        await call.answer("⭐ Already in your Favorites — it's saved and ready whenever you are.", show_alert=True)
        return
    await db.safe_insert("favorites", {
        "user_id": uid, "file_unique_id": fuid,
        "name": f.get("name"), "ext": f.get("ext"), "kind": f.get("kind"),
        "chan_id": f.get("chan_id"), "msg_id": f.get("msg_id"), "file_id": f.get("file_id"),
        "added_at": datetime.now(timezone.utc),
    })
    await call.answer("⭐ Saved to your Favorites — re-open it free, any time, from My Library.")


@router.callback_query(F.data == "lib_favorites")
async def cb_favorites(call: CallbackQuery) -> None:
    await call.answer()
    await _render(call, 0)


@router.callback_query(F.data.startswith("fav_pg:"))
async def cb_fav_page(call: CallbackQuery) -> None:
    await call.answer()
    await _render(call, int(call.data.split(":", 1)[1]))


async def _render(call: CallbackQuery, page: int) -> None:
    uid = call.from_user.id
    db = await MongoManager.get()
    items = await db.find_global("favorites", {"user_id": uid},
                                 sort=[("added_at", -1)])
    if not items:
        await call.message.edit_text(
            "⭐ <b>Your Favorites</b>\n"
            "<i>A private shelf for the titles you love most.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>It's quiet here for now — but not for long.\n\n"
            "Tap <b>⭐ Add to Favorites</b> on any book or audiobook you receive, "
            "and it lands here instantly. Re-open anything you've saved as often as "
            "you like — re-delivery is always <b>free</b>.</blockquote>\n"
            "<i>💡 Search a title, then save it — your collection starts with one tap.</i>",
            reply_markup=kb([btn("🔙 Back to Library", "menu_library", style="danger")]))
        return

    total = len(items)
    pages = (total + _PER_PAGE - 1) // _PER_PAGE
    page = max(0, min(page, pages - 1))
    chunk = items[page * _PER_PAGE:(page + 1) * _PER_PAGE]

    rows = []
    for f in chunk:
        fuid = f["file_unique_id"]
        ext = (f.get("ext") or "").lower()
        name = f.get("name", "Untitled")[:32]
        is_audio = f.get("kind") == "audio" or ext in _AUDIO_EXT
        # title row
        rows.append([btn(f"{icon_for(ext)} {name}", f"fav_get:{fuid}", style="primary")])
        # action row: open in the universal viewer (routes by type) · chat · remove
        open_btn = webapp_btn(
            "🎧 Listen" if is_audio else "📖 Read",
            "view.html", query=f"fuid={fuid}&ext={ext}",
            style="success", fallback_cb=f"fav_get:{fuid}")
        rows.append([open_btn,
                     btn("📥 Send to chat", f"fav_get:{fuid}", style="primary"),
                     btn("⭐ Rate", f"rate:{fuid}", style="primary"),
                     btn("🗑", f"fav_del:{fuid}", style="danger")])
    nav = []
    if page > 0:
        nav.append(btn("⬅️ Previous", f"fav_pg:{page-1}", style="primary"))
    if page + 1 < pages:
        nav.append(btn("Next ➡️", f"fav_pg:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 Back to Library", "menu_library", style="danger")])

    await call.message.edit_text(
        f"⭐ <b>Your Favorites</b>\n"
        f"<i>Curated by you — open any title free, as often as you like.</i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>{total}</b> saved · page <code>{page + 1}/{pages}</code>\n"
        f"<i>💡 Read or listen in the in-app viewer, or have it sent straight to chat.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("fav_get:"))
async def cb_fav_get(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    f = await db.find_one_global("favorites", {"user_id": uid, "file_unique_id": fuid})
    if not f:
        await call.answer("⚠️ This title isn't in your Favorites anymore — save it again to keep it on hand.", show_alert=True)
        return
    await call.answer("📤 On its way — delivering from your Favorites…")
    caption = (f"{icon_for(f.get('ext',''))} <b>{escape(f.get('name','Your File') or 'Your File')}</b>"
               "\n\n⭐ <i>Re-delivered from your Favorites — always free.</i>")
    ext = (f.get("ext") or "").lower()
    is_audio = f.get("kind") == "audio" or ext in _AUDIO_EXT
    rk = None
    if BOT_PUBLIC_URL:
        rk = kb([webapp_btn("🎧 Listen now" if is_audio else "📖 Read now",
                            "view.html", query=f"fuid={fuid}&ext={ext}", style="success")])
    src_channel = f.get("chan_id") or await get_file_channel()
    try:
        if src_channel and f.get("msg_id"):
            await call.bot.copy_message(uid, src_channel, f["msg_id"], caption=caption, reply_markup=rk)
        elif f.get("file_id"):
            await call.bot.send_document(uid, f["file_id"], caption=caption, reply_markup=rk)
        else:
            await call.message.answer("⚠️ <b>This title is no longer retrievable.</b>\n<i>The source file has moved on. Search for it again and we'll re-deliver a fresh copy.</i>")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Favorite re-delivery failed: %s", exc)
        await call.message.answer("⚠️ <b>We couldn't fetch this one right now.</b>\n<i>A momentary hiccup — please tap to open it again in a few seconds.</i>")


def _streak(days: list[str]) -> int:
    """Consecutive-day streak ending today or yesterday, from YYYY-MM-DD list."""
    s = set(days or [])
    if not s:
        return 0
    today = datetime.now(timezone.utc).date()
    cur = today if today.strftime("%Y-%m-%d") in s else today - timedelta(days=1)
    if cur.strftime("%Y-%m-%d") not in s:
        return 0
    n = 0
    while cur.strftime("%Y-%m-%d") in s:
        n += 1
        cur -= timedelta(days=1)
    return n


@router.callback_query(F.data == "lib_stats")
async def cb_reading_stats(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    db = await MongoManager.get()
    u = await db.find_one_global("users", {"user_id": uid}, {"reading_days": 1}) or {}
    days = u.get("reading_days") or []
    states = await db.find_global("reader_state", {"user_id": uid}, proj={"bookmarks": 1})
    in_progress = len(states)
    bookmarks = sum(len(s.get("bookmarks") or []) for s in states)
    favs = await db.count_global("favorites", {"user_id": uid})
    streak = _streak(days)
    fire = "🔥" * min(streak, 5) if streak else "—"
    await call.message.edit_text(
        "📊 <b>My Reading</b>\n"
        "<i>Your reading life, kept beautifully in one place.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"🔥 <b>Current streak</b> · <code>{streak}</code> day(s) {fire}\n"
        f"📅 <b>Days read</b> · <code>{len(days)}</code>\n"
        f"📖 <b>In progress</b> · <code>{in_progress}</code>\n"
        f"🔖 <b>Bookmarks</b> · <code>{bookmarks}</code>\n"
        f"⭐ <b>Favorites</b> · <code>{favs}</code>"
        "</blockquote>\n"
        "<i>💡 Open a book today to keep your streak alive — every day counts.</i>",
        reply_markup=kb([btn("📖 Continue Reading", "lib_continue", style="success")],
                        [btn("🔙 Back to Library", "menu_library", style="danger")]))


@router.callback_query(F.data == "lib_continue")
async def cb_continue(call: CallbackQuery) -> None:
    """Continue-Reading shelf — recently opened files with one-tap resume."""
    await call.answer()
    uid = call.from_user.id
    db = await MongoManager.get()
    states = await db.find_global("reader_state", {"user_id": uid},
                                  sort=[("updated_at", -1)], limit=8)
    if not states:
        await call.message.edit_text(
            "📖 <b>Continue Reading</b>\n"
            "<i>Your bookmark, saved automatically — pick up exactly where you left off.</i>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Nothing in progress just yet.\n\n"
            "Open any book or audiobook and we'll remember your place — your page, "
            "your position, your speed. It'll appear right here, ready to resume in one tap.</blockquote>\n"
            "<i>💡 Start a title from your Favorites and it lands on this shelf.</i>",
            reply_markup=kb([btn("🔙 Back to Library", "menu_library", style="danger")]))
        return
    rows = []
    for st in states:
        fuid = st.get("fuid")
        fav = await db.find_one_global("favorites", {"user_id": uid, "file_unique_id": fuid})
        if not fav:
            continue
        ext = (fav.get("ext") or "").lower()
        label = f"{icon_for(ext)} {fav.get('name','Untitled')[:32]}"
        rows.append([webapp_btn(label, "view.html", query=f"fuid={fuid}&ext={ext}",
                                style="success", fallback_cb=f"fav_get:{fuid}")])
    rows.append([btn("🔙 Back to Library", "menu_library", style="danger")])
    await call.message.edit_text(
        "📖 <b>Continue Reading</b>\n"
        "<i>Right where you left off — every page and position remembered.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Tap a title below to resume instantly. Your bookmark, theme and "
        "place carry over automatically — no setup, no scrolling.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("fav_del:"))
async def cb_fav_del(call: CallbackQuery) -> None:
    uid = call.from_user.id
    fuid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    for idx in db.healthy:
        await db.dbs[idx]["favorites"].delete_one({"user_id": uid, "file_unique_id": fuid})
    await call.answer("🗑 Removed from your Favorites. You can save it again any time.")
    await _render(call, 0)
