"""
handlers/arena.py — the Request Arena (group-topic request flow).

Direct in-bot requesting is a Premium perk. FREE members request in a public group
"Request Arena" — they post a book/audiobook name in ONE configured forum topic and
the bot finds it and delivers it to their DM (premium members may use it too). The
bot ONLY ever reacts to messages in the configured group + topic.

Flow (all bot replies stay in the topic, as a reply to the user):
  • Match found  → "Found N matches" + a deep-link button that opens the bot DM and,
    behind the join gate, delivers the file (premium = unlimited; free = daily quota,
    then a per-file overage/Premium upsell shown right in the topic).
  • No match     → "not in the library yet" + 🔔 Notify me (auto-DM when it's added)
    and 👤 Request from admins (concierge flow, pre-seeded with the title).
  • Notify me, if the user never started the bot, first asks them to start it once
    (a Start deep-link) — on start they get the dashboard + a "we'll notify you" note.

Mechanics: every action is a short-lived ticket (arena_tickets, TTL) referenced by a
deep-link `?start=ar_<token>` (handled in handlers/start.cmd_start → handle_ticket
here) or a callback (`arn_<token>` for Notify me). The join/force-sub gate + the
quota/overage/Premium logic are reused from start.py / request.py — nothing is
duplicated.

OPERATIONAL: the bot must receive the topic's messages — either be an ADMIN of the
group, or have BotFather group-privacy mode OFF. Configure the group + topic in
Admin → 🧰 More Tools → 📣 Request Arena (defaults: @free_novellas, topic 33).
"""
import logging
import secrets
import time
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import bot_username
from database.connection import MongoManager
from utils.keyboards import btn, cancel_row, kb, url_btn
from utils.permissions import is_super

logger = logging.getLogger(__name__)
router = Router()

_DEFAULT_CHAT = "free_novellas"
_DEFAULT_TOPIC = 33
_COOLDOWN_SEC = 8          # per-user anti-spam in the arena
_TICKET_TTL_DAYS = 7
# The Arena is a public free-for-all, so a loose fuzzy match (random syllables
# overlapping a title) is worse than an honest "not found". Hold matches to a
# stricter similarity bar than in-bot search so gibberish doesn't return a flood.
_ARENA_FUZZY_MIN = 0.62

_last_req: dict[int, float] = {}   # uid → monotonic ts of last arena request


def _bot_un() -> str:
    return bot_username()


def _now():
    return datetime.now(timezone.utc)


# ── settings (kv) ────────────────────────────────────────────────────────────────
async def _kv(key: str, default=None):
    db = await MongoManager.get()
    return await db.kv_get(key, default)


async def _kv_set(key: str, value) -> None:
    db = await MongoManager.get()
    await db.kv_set(key, value)


_cfg_cache: dict = {"t": 0.0, "v": None}


def _bust_cfg() -> None:
    _cfg_cache["v"] = None


async def cfg() -> dict:
    # Cached ~30s: this runs on EVERY group message the bot sees, so avoid 3 kv reads
    # per message in busy groups. Admin writes bust the cache immediately.
    now = time.monotonic()
    if _cfg_cache["v"] is not None and now - _cfg_cache["t"] < 30:
        return _cfg_cache["v"]
    v = {
        "enabled": bool(await _kv("arena_enabled", True)),
        "chat": str(await _kv("arena_chat", _DEFAULT_CHAT) or "").strip(),
        "topic": int(await _kv("arena_topic", _DEFAULT_TOPIC) or 0),
    }
    _cfg_cache.update(t=now, v=v)
    return v


async def topic_url() -> str:
    """Public https link to the arena topic (empty for a numeric/private chat id)."""
    c = await cfg()
    chat = c["chat"].lstrip("@")
    if not chat or chat.lstrip("-").isdigit():
        return ""
    return f"https://t.me/{chat}/{c['topic']}" if c["topic"] else f"https://t.me/{chat}"


async def _is_arena(message: Message) -> bool:
    c = await cfg()
    if not c["enabled"] or not c["chat"]:
        return False
    chat = c["chat"].lstrip("@")
    ok_chat = (str(message.chat.id) == chat
               or (message.chat.username or "").lower() == chat.lower())
    if not ok_chat:
        return False
    if c["topic"] and (message.message_thread_id or 0) != c["topic"]:
        return False
    return True


# ── tickets ──────────────────────────────────────────────────────────────────────
async def _new_ticket(kind: str, query: str, uid: int, fuid: str | None = None) -> str:
    token = secrets.token_urlsafe(9)[:14]
    db = await MongoManager.get()
    await db.safe_insert("arena_tickets", {
        "token": token, "kind": kind, "query": (query or "")[:200],
        "uid": uid, "fuid": fuid, "created_at": _now()})
    return token


async def _get_ticket(token: str) -> dict | None:
    db = await MongoManager.get()
    return await db.find_one_global("arena_tickets", {"token": token})


async def _has_started(uid: int) -> bool:
    db = await MongoManager.get()
    return bool(await db.find_one_global("users", {"user_id": uid}, {"_id": 1}))


def _start_url(token: str) -> str:
    return f"https://t.me/{_bot_un()}?start=ar_{token}"


# ── the topic listener ─────────────────────────────────────────────────────────────
@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def on_arena_message(message: Message) -> None:
    if not await _is_arena(message):
        return
    u = message.from_user
    if u is None or u.is_bot:
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/") or len(text) < 2:
        return
    now = time.monotonic()
    if now - _last_req.get(u.id, 0.0) < _COOLDOWN_SEC:
        return
    _last_req[u.id] = now
    try:
        await _handle_request(message, u.id, text)
    except Exception as exc:  # noqa: BLE001 — never crash on a group message
        logger.error("arena request error: %s", exc, exc_info=True)


async def _reply(message: Message, text: str, markup) -> None:
    try:
        await message.reply(text, reply_markup=markup)
    except Exception:  # noqa: BLE001 — fall back to a threaded answer
        try:
            await message.answer(text, reply_markup=markup,
                                 message_thread_id=message.message_thread_id)
        except Exception:  # noqa: BLE001
            pass


async def _handle_request(message: Message, uid: int, query: str) -> None:
    from utils.files import search, fuzzy_search
    from utils import premium, quota
    results, total = await search(query, limit=8)
    if total == 0:
        results, total = await fuzzy_search(query, limit=8, min_score=_ARENA_FUZZY_MIN)
    disp = escape(query[:60])
    un = _bot_un()

    if total > 0:
        top = results[0]
        multi = total > 1
        n_lbl = "match" if total == 1 else "matches"
        # SINGLE match → pin the file so a tap delivers it straight away. MANY →
        # carry NO fuid, so the deep-link opens a PICK LIST in DM and the requester
        # chooses which file they actually want (never an arbitrary auto-send).
        get_tok = await _new_ticket(
            "get", query, uid, fuid=(None if multi else top.get("file_unique_id")))
        get_url = f"https://t.me/{un}?start=ar_{get_tok}"
        if await premium.is_premium(uid) or await quota.can(uid, "dl"):
            cta = f"📚 See all {total} matches" if multi else "📥 Get the file"
            tail = ("pick the one you want and I'll send it to your DM. ✨" if multi
                    else "I'll prepare it and send it to your DM. ✨")
            await _reply(
                message,
                f"📚 <b>Found {total} {n_lbl}</b> for <i>{disp}</i>.\n"
                f"<blockquote>Tap below — {tail}</blockquote>",
                kb([url_btn(cta, get_url)]))
        elif multi:
            used, lim = await quota.status(uid, "dl")
            await _reply(
                message,
                f"📚 <b>Found {total} {n_lbl}</b> for <i>{disp}</i>.\n"
                f"⚠️ <b>You've used today's free downloads</b> "
                f"(<code>{used}/{quota.fmt_limit(lim)}</code>).\n"
                "<blockquote>Browse all the matches and grab one with a per-file pass, "
                "or go 👑 <b>Premium</b> for unlimited downloads.</blockquote>",
                kb([url_btn(f"📚 See all {total} matches", get_url)],
                   [url_btn("👑 Go Premium", f"https://t.me/{un}?start=go_premium")]))
        else:
            used, lim = await quota.status(uid, "dl")
            buy_tok = await _new_ticket("buy", query, uid, fuid=top.get("file_unique_id"))
            await _reply(
                message,
                f"📚 <b>Found {total} {n_lbl}</b> for <i>{disp}</i>.\n"
                f"⚠️ <b>You've used today's free downloads</b> "
                f"(<code>{used}/{quota.fmt_limit(lim)}</code>).\n"
                "<blockquote>Go 👑 <b>Premium</b> for unlimited downloads, or grab just "
                "this one now.</blockquote>",
                kb([url_btn("👑 Go Premium", f"https://t.me/{un}?start=go_premium")],
                   [url_btn("💳 Buy this file", f"https://t.me/{un}?start=ar_{buy_tok}")]))
    else:
        ntok = await _new_ticket("notify", query, uid)
        rtok = await _new_ticket("reqadmin", query, uid)
        await _reply(
            message,
            f"😔 <b>“{disp}” isn't in our library yet.</b>\n"
            "<blockquote>I can ping you the moment it's added, or our admins can source "
            "it for you by hand.</blockquote>",
            kb([btn("🔔 Notify me", f"arn_{ntok}", style="success")],
               [url_btn("👤 Request from admins", f"https://t.me/{un}?start=ar_{rtok}")]))


# ── Notify-me callback (in the topic) ──────────────────────────────────────────────
@router.callback_query(F.data.startswith("arn_"))
async def cb_notify(call: CallbackQuery) -> None:
    token = call.data[4:]
    t = await _get_ticket(token)
    if not t:
        await call.answer("This request expired — post the title again in the Arena.", show_alert=True)
        return
    uid = call.from_user.id
    # Ownership: only the reader who posted this request may use its buttons. Anyone
    # else is told (via the alert) to make their own request.
    if t.get("uid") and uid != t["uid"]:
        await call.answer(
            "🙋 This isn't your request — post the book's name in the Arena yourself "
            "and you'll get your own buttons.", show_alert=True)
        return
    query = t.get("query") or ""
    if await _has_started(uid):
        from handlers.request import _add_watchlist
        await _add_watchlist(uid, query)
        await call.answer("🔔 Done! I'll DM you the moment it's added.", show_alert=True)
    else:
        await call.answer()
        await _reply(
            call.message,
            f"🔔 To get notified about <i>{escape(query[:60])}</i>, start me once first 👇",
            kb([url_btn("▶️ Start the bot", _start_url(token))]))


async def _deny_foreign_ticket(message: Message) -> None:
    """Someone tapped a deep-link for a request that isn't theirs. Explain it, point
    them at the Arena, and still drop them onto the normal dashboard (never a dead end)."""
    url = await topic_url()
    rows = [[url_btn("📣 Open the Request Arena", url)]] if url else []
    await message.answer(
        "🙋 <b>That request belongs to another reader</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Those buttons were created for whoever asked for this title in the "
        "Request Arena. To get your own copy, just post the book or audiobook name in the "
        "Arena yourself — I'll line it up and send it straight to you.</blockquote>",
        reply_markup=(kb(*rows) if rows else None))
    try:
        from handlers.start import _render_gate_or_dashboard
        await _render_gate_or_dashboard(message)
    except Exception:  # noqa: BLE001 — onboarding is best-effort here
        pass


# ── deep-link dispatcher (called from handlers/start.cmd_start) ──────────────────────
async def handle_ticket(message: Message, state: FSMContext, uid: int, token: str) -> None:
    """Run a ticket's action in the user's DM, behind the join/force-sub gate."""
    t = await _get_ticket(token)
    if not t:
        await message.answer(
            "⏳ <b>This request link has expired</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>No worries — just post the title again in the Request Arena and "
            "I'll line it up fresh.</blockquote>")
        return
    # Ownership: this ticket belongs to whoever placed the request in the Arena. If a
    # DIFFERENT user opened the deep-link, never act on their behalf — onboard them and
    # point them to make their own request.
    if t.get("uid") and uid != t["uid"]:
        await _deny_foreign_ticket(message)
        return
    # force-sub / join gate (reuse start.py) — user re-taps the link after joining
    from handlers.start import _not_joined, _join_kb
    missing = await _not_joined(message.bot, uid)
    if missing:
        await message.answer(
            "👋 <b>Almost there</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Join our official channels below to unlock the archive, then tap "
            "your link from the group once more — your title will be waiting.</blockquote>",
            reply_markup=_join_kb(missing))
        return

    kind = t.get("kind")
    query = t.get("query") or ""
    fuid = t.get("fuid")
    if kind == "get":
        await _fulfil_query(message, uid, query, fuid)
    elif kind == "buy":
        from utils.files import get_file
        f = await get_file(fuid) if fuid else None
        if not f:
            await _fulfil_query(message, uid, query, None)
            return
        from handlers.request import fulfil_paid
        await fulfil_paid(message.bot, message, uid, f)
    elif kind == "notify":
        from handlers.request import _add_watchlist
        await _add_watchlist(uid, query)
        from handlers.start import _send_dashboard
        await _send_dashboard(message, message.from_user.first_name or "Reader")
        await message.answer(
            "🔔 <b>You're on the list!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>The moment <b>{escape(query[:80])}</b> is added to the archive, "
            "I'll send it straight to this chat. No need to check back.</blockquote>")
    elif kind == "reqadmin":
        from handlers.requests_manual import begin_concierge
        await begin_concierge(message, state, prefill_title=query)
    else:
        await message.answer("This request link is no longer valid — post the title again.")


async def _fulfil_query(message: Message, uid: int, query: str, fuid: str | None) -> None:
    """Deliver a matched file (single → straight to delivery; many → a pick list).
    Re-resolves from the live archive so a stale ticket fuid can't misfire."""
    from utils.files import get_file, search, fuzzy_search, icon_for
    from handlers.request import fulfil_download
    if fuid:
        f = await get_file(fuid)
        if f:
            await fulfil_download(message.bot, message, uid, f)
            return
    results, total = await search(query, limit=8)
    if total == 0:
        results, total = await fuzzy_search(query, limit=8, min_score=_ARENA_FUZZY_MIN)
    if total == 0:
        await message.answer(
            f"🔭 <b>“{escape(query[:60])}” has moved on</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>It's not in the archive right now. Post the title again in the "
            "Arena and I'll keep looking.</blockquote>")
        return
    if total == 1:
        await fulfil_download(message.bot, message, uid, results[0])
        return
    from utils import prepare
    cm = await prepare.clean_names_for(results)
    rows = [[btn(f"{icon_for(r.get('ext', ''))} {(cm.get(r['file_unique_id']) or r.get('name', 'Untitled'))[:38]}",
                 f"dl:{r['file_unique_id']}", style="success")] for r in results]
    shown = len(results)
    more = (f"\n<i>Showing the closest {shown} — add a few more words to your title in "
            "the Arena to narrow it down.</i>") if total > shown else ""
    await message.answer(
        f"📚 <b>{total} matches for</b> <i>{escape(query[:60])}</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Tap the one you want and I'll send it over.</blockquote>"
        f"{more}",
        reply_markup=kb(*rows))


# ── admin config panel (🧰 More Tools → 📣 Request Arena) ────────────────────────────
class ArenaFSM(StatesGroup):
    chat = State()
    topic = State()


async def _panel() -> tuple[str, object]:
    c = await cfg()
    url = await topic_url()
    link = f"<a href=\"{url}\">{escape(c['chat'])}/{c['topic']}</a>" if url else f"<code>{escape(c['chat'] or '—')}</code> / topic <code>{c['topic']}</code>"
    text = (
        "📣 <b>Request Arena</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>The public group topic where free members request books — the bot finds &amp; "
        "delivers them to DMs. Direct in-bot requests stay Premium-only.</i>\n"
        "<blockquote>"
        f"⚙️ <b>Listening:</b> {'🟢 ON' if c['enabled'] else '🔴 OFF'}\n"
        f"💬 <b>Group/topic:</b> {link}"
        "</blockquote>\n"
        "<i>⚠️ The bot must be an <b>admin</b> of that group (or have group-privacy mode "
        "OFF) to read topic messages.</i>"
    )
    rows = [
        [btn("🔴 Turn OFF" if c["enabled"] else "🟢 Turn ON", "arena_toggle",
             style="danger" if c["enabled"] else "success")],
        [btn("💬 Set Group", "arena_set_chat", style="primary"),
         btn("🔢 Set Topic", "arena_set_topic", style="primary")],
        [btn("🔙 More Tools", "admin_more", style="primary")],
    ]
    return text, kb(*rows)


@router.callback_query(F.data == "admin_arena")
async def cb_arena_admin(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only — this tool is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "arena_toggle")
async def cb_arena_toggle(call: CallbackQuery) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    c = await cfg()
    await _kv_set("arena_enabled", not c["enabled"])
    _bust_cfg()
    await call.answer("🟢 Arena ON." if not c["enabled"] else "🔴 Arena paused.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "arena_set_chat")
async def cb_arena_set_chat(call: CallbackQuery, state: FSMContext) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ArenaFSM.chat)
    await call.message.answer(
        "💬 <b>Set Arena Group</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the group's <b>@username</b> (e.g. <code>@free_novellas</code>) "
        "or its numeric chat id (e.g. <code>-1001234567890</code>).</blockquote>",
        reply_markup=kb(cancel_row("admin_arena")))


@router.message(ArenaFSM.chat, F.text)
async def on_arena_chat(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Unchanged."); return
    await state.clear()
    await _kv_set("arena_chat", raw)
    _bust_cfg()
    text, markup = await _panel()
    await message.answer("✅ <b>Arena group set.</b>")
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "arena_set_topic")
async def cb_arena_set_topic(call: CallbackQuery, state: FSMContext) -> None:
    if not is_super(call.from_user.id):
        await call.answer("🔒 Owner only.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ArenaFSM.topic)
    await call.message.answer(
        "🔢 <b>Set Arena Topic</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Send the forum <b>topic id</b> — the number at the end of the topic "
        "link (e.g. <code>33</code> in <code>t.me/free_novellas/33</code>). "
        "Send <code>0</code> to listen to the whole group.</blockquote>",
        reply_markup=kb(cancel_row("admin_arena")))


@router.message(ArenaFSM.topic, F.text)
async def on_arena_topic(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ Unchanged."); return
    if not raw.lstrip("-").isdigit():
        await message.answer("⚠️ Send a number (the topic id), e.g. <code>33</code>.")
        return
    await state.clear()
    await _kv_set("arena_topic", int(raw))
    _bust_cfg()
    text, markup = await _panel()
    await message.answer("✅ <b>Arena topic set.</b>")
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("arena"))
async def cmd_arena(message: Message) -> None:
    if not message.from_user or not is_super(message.from_user.id):
        return
    text, markup = await _panel()
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)
