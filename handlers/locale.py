"""
handlers/locale.py — 🌐 Language & Currency settings.

Account → 🌐 Language: pick a UI language (applied to translated surfaces) and a
display currency (shows BGM prices in your currency — display only).
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import MIN_BGM_PURCHASE
from utils.currency import CURRENCIES, fmt as fmt_cur, get_currency, set_currency
from utils.i18n import LANGUAGES, get_lang, set_lang, t
from utils.keyboards import btn, kb
from utils.settings import get_float

logger = logging.getLogger(__name__)
router = Router()


async def _hub(uid: int):
    lang = await get_lang(uid)
    cur = await get_currency(uid)
    return (
        "🌐 <b>Language &amp; Currency</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Make the bot speak your language and show prices the way you think about them.</i>\n\n"
        "<blockquote>"
        f"🗣 Language · <b>{LANGUAGES.get(lang, lang)}</b>\n"
        f"💱 Currency · <b>{cur}</b>"
        "</blockquote>\n"
        "<i>💡 Currency is for display only — it never changes how you actually pay.</i>",
        kb([btn("🗣 Change Language", "loc_lang", style="primary"),
            btn("💱 Change Currency", "loc_cur", style="primary")],
           [btn("🔙 Back to Account", "menu_account", style="danger")]))


@router.callback_query(F.data == "menu_locale")
async def cb_hub(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = await _hub(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


@router.message(Command("language"))
async def cmd_lang(message: Message) -> None:
    text, markup = await _hub(message.chat.id)
    await message.answer(text, reply_markup=markup)


# ── language ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "loc_lang")
async def cb_lang(call: CallbackQuery) -> None:
    await call.answer()
    lang = await get_lang(call.from_user.id)
    rows, row = [], []
    for code, label in LANGUAGES.items():
        mark = "✅ " if code == lang else ""
        row.append(btn(f"{mark}{label}", f"loc_setlang:{code}", style="primary"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([btn("🔙 Back", "menu_locale", style="danger")])
    await call.message.edit_text(
        f"🗣 <b>{t('pick_lang', lang)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Tap a language to translate the bot's main surfaces instantly.</i>\n\n"
        "<i>💡 The ✅ marks your current choice — you can switch back any time.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("loc_setlang:"))
async def cb_setlang(call: CallbackQuery) -> None:
    code = call.data.split(":", 1)[1]
    if code not in LANGUAGES:
        await call.answer(); return
    await set_lang(call.from_user.id, code)
    await call.answer(f"{t('lang_set', code)} ✅", show_alert=True)
    text, markup = await _hub(call.from_user.id)
    await call.message.edit_text(text, reply_markup=markup)


# ── currency ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "loc_cur")
async def cb_cur(call: CallbackQuery) -> None:
    await call.answer()
    cur = await get_currency(call.from_user.id)
    usd_price = await get_float("bgm_price_usd")
    min_cost_usd = usd_price * MIN_BGM_PURCHASE
    rows, row = [], []
    for code in CURRENCIES:
        mark = "✅ " if code == cur else ""
        row.append(btn(f"{mark}{code}", f"loc_setcur:{code}", style="primary"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([btn("🔙 Back", "menu_locale", style="danger")])
    await call.message.edit_text(
        "💱 <b>Display Currency</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>See 💎 BGM prices in the currency you think in — clearer at a glance.</i>\n\n"
        "<blockquote>"
        f"📊 Smallest top-up · <code>{MIN_BGM_PURCHASE} BGM</code> ≈ <b>{fmt_cur(min_cost_usd, cur)}</b>\n"
        "💡 This is a preview only — checkout still settles in UPI (₹) or crypto ($)."
        "</blockquote>\n"
        "<i>Tap a currency below — the ✅ shows your current pick.</i>",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("loc_setcur:"))
async def cb_setcur(call: CallbackQuery) -> None:
    code = call.data.split(":", 1)[1]
    if code not in CURRENCIES:
        await call.answer(); return
    await set_currency(call.from_user.id, code)
    await call.answer(f"Prices now shown in {code} ✅")
    await cb_cur(call)
