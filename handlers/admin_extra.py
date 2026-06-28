"""
handlers/admin_extra.py — extra admin governance tools.

Admin panel → 🧰 More Tools:
  🔨 Bulk Ban       — ban many user IDs at once
  📜 Audit Log      — recent admin actions (utils.audit)
  🚩 Feature Flags  — turn features on/off live (utils.flags)
"""
import json
import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from pymongo import DESCENDING

from config import ADMIN_IDS, SUPER_ADMIN_ID
from database.connection import MongoManager
from utils.audit import log_action, recent
from utils.coupons import active_coupons, create_coupon
from utils.flags import FLAGS, all_flags, is_on, set_flag
from utils.format import fmt_amount, valid_amount
from utils.keyboards import btn, kb
from utils.users import set_ban

logger = logging.getLogger(__name__)
router = Router()

# collections that hold per-user data (queried by user_id OR uid)
_USER_COLLECTIONS = [
    "users", "favorites", "library", "requests", "watchlist", "reader_state", "code_claims",
    "payments", "crypto_orders", "reports", "game_sessions", "game_progress",
    "bookle_sessions",
]


class ExtraFSM(StatesGroup):
    bulk_ban = State()
    gdpr_uid = State()
    cpn_value = State()
    cpn_uses = State()
    cpn_days = State()


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


@router.callback_query(F.data == "admin_more")
async def cb_more(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is for administrators only.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🛡 <b>Advanced Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>The deeper governance toolkit — moderation, compliance, growth levers and live health.</i>\n"
        "<blockquote>🔨 <b>Moderation</b> — bulk bans, reports and auto-mod keep the library safe.\n"
        "🎟 <b>Growth</b> — coupons, happy hour and surge pricing tune the economy in real time.\n"
        "🧹 <b>Compliance</b> — GDPR export and erase, plus duplicate cleanup.\n"
        "📊 <b>Insight</b> — audit trail, health checks, risk signals and live experiments.</blockquote>\n"
        "<i>💡 Pick a tool below — every change applies instantly, no redeploy needed.</i>",
        reply_markup=kb(
            [btn("🔨 Bulk Ban", "admin_bulkban", style="danger"),
             btn("🚩 Reports", "admin_reports", style="primary")],
            [btn("📜 Audit Log", "admin_audit", style="primary"),
             btn("🚩 Feature Flags", "admin_flags", style="primary")],
            [btn("🎟️ Coupons", "admin_coupons", style="success"),
             btn("🧹 GDPR Tools", "admin_gdpr", style="danger")],
            [btn("⚡ Happy Hour", "admin_happy", style="success"),
             btn("📈 Surge Pricing", "admin_surge", style="primary")],
            [btn("📢 Ad Slots", "admin_ads", style="success"),
             btn("🩺 Health", "admin_health", style="primary")],
            [btn("🛡 Auto-Mod", "admin_mod", style="primary"),
             btn("🧹 Duplicates", "admin_dedupe", style="danger")],
            [btn("🚨 Risk / Fraud", "admin_risk", style="danger"),
             btn("🧪 A/B Test", "admin_abtest", style="success")],
            [btn("🔙 Back", "admin_open", style="primary")]))


# ── bulk ban ─────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_bulkban")
async def cb_bulkban(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is for administrators only.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ExtraFSM.bulk_ban)
    await call.message.answer(
        "🔨 <b>Bulk Ban</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Remove several accounts in one pass — fast and reversible from the user lookup.</i>\n"
        "<blockquote>📋 Paste the <b>User IDs</b> you want to ban.\n"
        "Separate them with <b>spaces, commas or new lines</b> — mix and match freely.\n"
        "🛡 The super admin is always protected and will be skipped automatically.</blockquote>\n"
        "<i>💡 Send <code>/cancel</code> any time to back out — nothing happens until you submit.</i>")


@router.message(ExtraFSM.bulk_ban, F.text)
async def on_bulkban(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b> No accounts were touched."); return
    await state.clear()
    ids = [int(x) for x in re.split(r"[\s,]+", raw) if x.lstrip("-").isdigit()]
    ids = [i for i in dict.fromkeys(ids) if i > 0 and i != SUPER_ADMIN_ID]
    if not ids:
        await message.answer(
            "⚠️ <b>No valid User IDs found</b>\n"
            "<i>Send numeric IDs separated by spaces, commas or new lines, then try again.</i>")
        return
    for uid in ids:
        await set_ban(uid, True)
    await log_action(message.chat.id, "bulk_ban", f"{len(ids)} users")
    await message.answer(
        f"✅ <b>Bulk ban complete</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🔨 Banned <b>{len(ids)}</b> account(s) in this batch.\n"
        "Duplicates and the super admin were skipped automatically.</blockquote>\n"
        "<i>💡 Need to reverse one? Reinstate it from the user lookup at any time.</i>",
        reply_markup=kb([btn("🔙 More Tools", "admin_more", style="primary")]))


# ── audit log ────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_audit")
async def cb_audit(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is for administrators only.", show_alert=True)
        return
    await call.answer()
    rows = await recent(20)
    if not rows:
        body = ("<blockquote><i>No admin actions logged yet — the trail starts the moment "
                "your team makes its first change.</i></blockquote>")
    else:
        lines = []
        for r in rows:
            at = r.get("at")
            ts = at.strftime("%d %b %H:%M") if hasattr(at, "strftime") else "—"
            detail = f" · {escape(str(r['detail']))}" if r.get("detail") else ""
            lines.append(f"<code>{ts}</code> · <code>{r.get('admin_id')}</code> · "
                         f"<b>{escape(str(r.get('action') or ''))}</b>{detail}")
        body = "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"
    await call.message.edit_text(
        "📜 <b>Audit Log</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>The last 20 admin actions — who did what, and when.</i>\n" + body,
        reply_markup=kb([btn("🔄 Refresh", "admin_audit", style="primary")],
                        [btn("🔙 Back", "admin_more", style="primary")]))


# ── feature flags (super admin) ──────────────────────────────────────────────
@router.callback_query(F.data == "admin_flags")
async def cb_flags(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    flags = await all_flags()
    rows = []
    for key, label in FLAGS.items():
        on = flags.get(key, True)
        rows.append([btn(f"{'🟢' if on else '🔴'} {label}", f"flag:{key}",
                         style="success" if on else "danger")])
    rows.append([btn("🔙 Back", "admin_more", style="primary")])
    await call.message.edit_text(
        "🚩 <b>Feature Flags</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Turn whole features on or off across the bot — instantly, no redeploy.</i>\n"
        "<blockquote>🟢 <b>Green</b> means the feature is live for everyone.\n"
        "🔴 <b>Red</b> means it's hidden and paused.\n"
        "Tap any row to flip it — the change takes effect right away.</blockquote>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("flag:"))
async def cb_flag_toggle(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key not in FLAGS:
        await call.answer("That feature flag no longer exists.", show_alert=True)
        return
    cur = await is_on(key)
    await set_flag(key, not cur)
    await log_action(call.from_user.id, "flag_toggle", f"{key}={'off' if cur else 'on'}")
    await call.answer(f"{FLAGS[key]} is now {'OFF' if cur else 'ON'}")
    await cb_flags(call)


# ── content reports ──────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_reports")
async def cb_reports(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is for administrators only.", show_alert=True)
        return
    await call.answer()
    db = await MongoManager.get()
    rows = await db.find_global("reports", {"status": "open"}, limit=10,
                                sort=[("created_at", DESCENDING)])
    if not rows:
        await call.message.edit_text(
            "🚩 <b>Content Reports</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>✨ <b>All clear</b> — there are no open reports right now.\n"
            "When a reader flags something, it will land here for review.</blockquote>",
            reply_markup=kb([btn("🔙 Back", "admin_more", style="primary")]))
        return
    lines = ["🚩 <b>Open Reports</b>\n"
             "━━━━━━━━━━━━━━━━━━\n"
             "<i>Newest first — review and resolve each flag below.</i>"]
    btns = []
    for r in rows:
        lines.append(f"<code>{r.get('rid')}</code> · 👤 <code>{r.get('user_id')}</code>\n"
                     f"{(r.get('text') or '')[:180]}")
        btns.append([btn(f"✅ Resolve {r.get('rid')}", f"rpt_done:{r.get('rid')}", style="success")])
    btns.append([btn("🔄 Refresh", "admin_reports", style="primary"),
                 btn("🔙 Back", "admin_more", style="primary")])
    await call.message.edit_text("\n\n".join(lines), reply_markup=kb(*btns))


@router.callback_query(F.data.startswith("rpt_done:"))
async def cb_report_done(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("This area is for administrators only.", show_alert=True)
        return
    rid = call.data.split(":", 1)[1]
    db = await MongoManager.get()
    await db.safe_update("reports", {"rid": rid}, {"$set": {"status": "resolved"}}, upsert=False)
    await log_action(call.from_user.id, "report_resolve", rid)
    await call.answer("Report resolved and cleared from the queue.")
    await cb_reports(call)


# ── GDPR: export / delete a user's data (super admin) ────────────────────────
@router.callback_query(F.data == "admin_gdpr")
async def cb_gdpr(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ExtraFSM.gdpr_uid)
    await call.message.answer(
        "🧹 <b>GDPR Tools</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Honour a reader's data request — export everything we hold, or erase it for good.</i>\n"
        "<blockquote>📤 <b>Export</b> packages every record we store for a user into a single JSON file.\n"
        "🗑 <b>Erase</b> permanently removes all of their data across every collection.</blockquote>\n"
        "<i>💡 Send the <b>User ID</b> to continue, or <code>/cancel</code> to step back.</i>")


@router.message(ExtraFSM.gdpr_uid, F.text)
async def on_gdpr_uid(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b> No data was touched."); return
    await state.clear()
    if not raw.isdigit():
        await message.answer(
            "⚠️ <b>That's not a valid User ID</b>\n"
            "<i>A User ID is numbers only — check it and send again.</i>")
        return
    await message.answer(
        f"🧹 <b>Data request — User <code>{raw}</code></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Choose how you'd like to handle this reader's data.</i>",
        reply_markup=kb([btn("📤 Export data (JSON)", f"gdpr_exp:{raw}", style="primary")],
                        [btn("🗑 Erase all data", f"gdpr_del:{raw}", style="danger")],
                        [btn("🔙 Back", "admin_more", style="primary")]))


async def _user_docs(db, uid: int):
    flt = {"$or": [{"user_id": uid}, {"uid": uid}]}
    out = {}
    for coll in _USER_COLLECTIONS:
        docs = []
        for idx in db.healthy:
            cur = db.dbs[idx][coll].find(flt)
            docs += [{k: v for k, v in d.items() if k != "_id"} async for d in cur]
        if docs:
            out[coll] = docs
    return out


@router.callback_query(F.data.startswith("gdpr_exp:"))
async def cb_gdpr_exp(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    uid = int(call.data.split(":", 1)[1])
    await call.answer("Gathering this reader's data…")
    db = await MongoManager.get()
    data = await _user_docs(db, uid)
    payload = json.dumps(data, default=str, indent=2, ensure_ascii=False).encode("utf-8")
    await call.message.answer_document(
        BufferedInputFile(payload, filename=f"user_{uid}_export.json"),
        caption=f"📤 <b>Data export ready</b>\n"
                f"<blockquote>👤 User <code>{uid}</code>\n"
                f"📦 <b>{len(data)}</b> collection(s) packaged into this file.</blockquote>")
    await log_action(call.from_user.id, "gdpr_export", str(uid))


@router.callback_query(F.data.startswith("gdpr_del:"))
async def cb_gdpr_del(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    uid = call.data.split(":", 1)[1]
    await call.answer()
    await call.message.edit_text(
        "⚠️ <b>Confirm permanent erasure</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🗑 You're about to erase <b>every record</b> for user <code>{uid}</code> "
        "across all collections.\n"
        "🔒 This is final — the data <b>cannot</b> be recovered afterwards.</blockquote>\n"
        "<i>Tap below only if you're certain.</i>",
        reply_markup=kb([btn("🗑 Yes, erase everything", f"gdpr_delc:{uid}", style="danger")],
                        [btn("❌ Cancel", "admin_more", style="primary")]))


@router.callback_query(F.data.startswith("gdpr_delc:"))
async def cb_gdpr_delc(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    uid = int(call.data.split(":", 1)[1])
    await call.answer("Erasing this reader's data…")
    db = await MongoManager.get()
    flt = {"$or": [{"user_id": uid}, {"uid": uid}]}
    removed = 0
    for coll in _USER_COLLECTIONS:
        for idx in db.healthy:
            res = await db.dbs[idx][coll].delete_many(flt)
            removed += res.deleted_count
    await log_action(call.from_user.id, "gdpr_delete", f"{uid} ({removed} docs)")
    await call.message.edit_text(
        "✅ <b>Erasure complete</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🗑 Removed <b>{removed}</b> document(s) for user <code>{uid}</code>.\n"
        "Their footprint has been cleared from every collection.</blockquote>",
        reply_markup=kb([btn("🔙 Back", "admin_more", style="primary")]))


# ── promo coupons (super admin) ──────────────────────────────────────────────
@router.callback_query(F.data == "admin_coupons")
async def cb_coupons(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    coupons = await active_coupons(15)
    lines = ["🎟️ <b>Promo Coupons</b>\n"
             "━━━━━━━━━━━━━━━━━━\n"
             "<i>Live bonus codes readers can apply at checkout under 💎 Buy BGM.</i>"]
    if not coupons:
        lines.append("<blockquote><i>No active coupons yet — spin one up below to reward "
                     "your readers and lift conversions.</i></blockquote>")
    for c in coupons:
        val = (f"{fmt_amount(c.get('value'))}%" if c.get("kind") == "pct"
               else f"{fmt_amount(c.get('value'))} BGM")
        exp = c.get("expires_at")
        exp_s = exp.strftime("%d %b") if hasattr(exp, "strftime") else "—"
        lines.append(f"<code>{c.get('code')}</code> · {val} · "
                     f"{int(c.get('uses') or 0)}/{int(c.get('max_uses') or 0)} used · exp {exp_s}")
    await call.message.edit_text(
        "\n".join(lines),
        reply_markup=kb([btn("➕ New Coupon", "cpn_new", style="success")],
                        [btn("🔙 Back", "admin_more", style="primary")]))


@router.callback_query(F.data == "cpn_new")
async def cb_cpn_new(call: CallbackQuery) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🎟️ <b>New Coupon</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Step 1 of 4 — pick how the bonus is calculated.</i>\n"
        "<blockquote>％ <b>Percent of purchase</b> — adds a share of whatever they buy, scaling with bigger orders.\n"
        "➕ <b>Flat BGM</b> — a fixed 💎 bonus on top, the same for every redemption.</blockquote>",
        reply_markup=kb([btn("％ Percent of purchase", "cpn_kind:pct", style="primary")],
                        [btn("➕ Flat BGM", "cpn_kind:flat", style="primary")],
                        [btn("🔙 Back", "admin_coupons", style="danger")]))


@router.callback_query(F.data.startswith("cpn_kind:"))
async def cb_cpn_kind(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != SUPER_ADMIN_ID:
        await call.answer("Reserved for the super admin.", show_alert=True)
        return
    kind = call.data.split(":", 1)[1]
    await state.update_data(cpn_kind=kind)
    await state.set_state(ExtraFSM.cpn_value)
    await call.answer()
    unit = "percent bonus, e.g. <code>20</code> for +20%" if kind == "pct" \
        else "flat 💎 BGM bonus, e.g. <code>5</code>"
    await call.message.answer(
        "🎟️ <b>New Coupon</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<i>Step 2 of 4 — set the reward size.</i>\n"
        f"<blockquote>💸 Enter the {unit}.</blockquote>\n"
        "<i>💡 Send <code>/cancel</code> to stop here.</i>")


@router.message(ExtraFSM.cpn_value, F.text)
async def on_cpn_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b> No coupon was created."); return
    ok, val = valid_amount(raw)
    if not ok:
        await message.answer(
            "⚠️ <b>That amount doesn't look right</b>\n"
            "<i>Enter a positive number — no <code>inf</code> or <code>1e21</code>.</i>")
        return
    if (await state.get_data()).get("cpn_kind") == "pct" and val > 100:
        await message.answer(
            "⚠️ <b>A percent bonus tops out at 100%</b>\n"
            "<i>Enter a value between <code>1</code> and <code>100</code>.</i>")
        return
    await state.update_data(cpn_value=val)
    await state.set_state(ExtraFSM.cpn_uses)
    await message.answer(
        "🎟️ <b>New Coupon</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Step 3 of 4 — set the redemption cap.</i>\n"
        "<blockquote>🧮 How many <b>total uses</b> should this code allow? e.g. <code>100</code>\n"
        "Once the cap is reached, the coupon retires itself automatically.</blockquote>")


@router.message(ExtraFSM.cpn_uses, F.text)
async def on_cpn_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b> No coupon was created."); return
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer(
            "⚠️ <b>Enter a whole number above zero</b>\n"
            "<i>For example <code>100</code> total uses.</i>"); return
    await state.update_data(cpn_uses=int(raw))
    await state.set_state(ExtraFSM.cpn_days)
    await message.answer(
        "🎟️ <b>New Coupon</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Step 4 of 4 — set the lifespan.</i>\n"
        "<blockquote>⏳ Valid for how many <b>days</b> from now? e.g. <code>30</code>\n"
        "After that, the code expires gracefully on its own.</blockquote>")


@router.message(ExtraFSM.cpn_days, F.text)
async def on_cpn_days(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear(); await message.answer("❌ <b>Cancelled.</b> No coupon was created."); return
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer(
            "⚠️ <b>Enter a whole number of days above zero</b>\n"
            "<i>For example <code>30</code> days.</i>"); return
    data = await state.get_data()
    await state.clear()
    code = await create_coupon(data["cpn_kind"], data["cpn_value"], data["cpn_uses"],
                               int(raw), message.chat.id)
    await log_action(message.chat.id, "coupon_create",
                     f"{code} {data['cpn_kind']}={data['cpn_value']:g}")
    val = (f"{fmt_amount(data['cpn_value'])}%" if data["cpn_kind"] == "pct"
           else f"{fmt_amount(data['cpn_value'])} BGM")
    await message.answer(
        "✅ <b>Coupon is live</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🎟️ Code: <code>{code}</code>\n"
        f"💸 Bonus: <b>{val}</b>\n"
        f"🧮 Uses: <code>{data['cpn_uses']}</code>\n"
        f"⏳ Valid for: <code>{raw}</code> day(s)</blockquote>\n"
        "<i>💡 Share the code — readers apply it under 💎 Buy BGM to unlock the bonus.</i>",
        reply_markup=kb([btn("🎟️ Coupons", "admin_coupons", style="primary")]))
