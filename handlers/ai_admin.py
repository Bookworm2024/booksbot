"""
handlers/ai_admin.py — super-admin AI provider configuration (no redeploy).

Admin panel → 🤖 AI Settings → switch the provider (Free API / Claude / Off),
set the free API URL, set a Claude key + model, and test the connection live.
Also configurable from the /admin Mini-App dashboard. Backed by utils.ai's kv
config, so changes take effect immediately for Recommendations, Summaries and
genre tagging.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from utils.ai import DEFAULT_FREE_URL, ai_complete, get_ai_config, set_ai_config
from utils.keyboards import btn, cancel_row, kb

logger = logging.getLogger(__name__)
router = Router()

_PROV_LABEL = {"free": "🆓 Free API (bots.lt)", "anthropic": "💎 Claude (Anthropic)", "off": "🚫 Off"}


class AIFSM(StatesGroup):
    free_url = State()
    key = State()
    model = State()
    webhook_url = State()


def _mask(s: str) -> str:
    if not s:
        return "—"
    return f"{s[:4]}…{s[-3:]}" if len(s) > 10 else "set"


async def _panel():
    cfg = await get_ai_config()
    prov = cfg["provider"]
    wh_on = cfg["webhook_enabled"]
    wh_url = cfg["webhook_url"] or "—"
    active = ("🪝 Custom Webhook" if (wh_on and cfg["webhook_url"])
              else _PROV_LABEL.get(prov, prov))
    text = (
        "🤖 <b>AI Engine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>The intelligence behind recommendations, summaries and tagging.</i>\n\n"
        "<blockquote>"
        f"⚡ <b>Active</b> · {active}\n"
        f"🔌 <b>Provider</b> · {_PROV_LABEL.get(prov, prov)}\n"
        f"🔗 <b>Free URL</b> · <code>{cfg['free_url']}</code>\n"
        f"🔑 <b>Claude key</b> · <code>{_mask(cfg['anthropic_key'])}</code>\n"
        f"🧠 <b>Claude model</b> · <code>{cfg['model']}</code>\n"
        f"🪝 <b>Webhook</b> · {'🟢 ON' if wh_on else '⚪ OFF'}\n"
        f"🌐 <b>Webhook URL</b> · <code>{wh_url}</code>"
        "</blockquote>\n"
        "<blockquote expandable>"
        "This engine powers 🤖 <b>Recommendations</b>, 📝 <b>Summaries</b> and "
        "🏷 <b>genre tagging</b> across the library.\n"
        "🆓 <b>Free API</b> — zero-cost, no key required, great for everyday use.\n"
        "💎 <b>Claude</b> — Anthropic's premium models for the sharpest, most "
        "natural results (needs an API key).\n"
        "🪝 <b>Webhook mode</b> — point the bot at any custom AI endpoint. When ON, "
        "every AI request is sent to your webhook URL and the reply is used "
        "everywhere — overrides the provider above.\n"
        "🚫 <b>Off</b> — pauses every AI feature instantly."
        "</blockquote>\n"
        "<i>💡 Changes apply live — no redeploy, no downtime.</i>"
    )
    rows = [
        [btn("🆓 Use Free API", "ai_prov:free",
             style="success" if prov == "free" else "primary"),
         btn("💎 Use Claude", "ai_prov:anthropic",
             style="success" if prov == "anthropic" else "primary")],
        [btn("🚫 Turn Off", "ai_prov:off",
             style="danger" if prov == "off" else "primary")],
        [btn("🔗 Set Free URL", "ai_set:free_url", style="primary"),
         btn("♻️ Reset URL", "ai_reset_url", style="primary")],
        [btn("🔑 Set Claude Key", "ai_set:key", style="primary"),
         btn("🧠 Set Model", "ai_set:model", style="primary")],
        [btn("🪝 Webhook: ON ✅" if wh_on else "🪝 Webhook: OFF",
             "ai_webhook_toggle", style="success" if wh_on else "primary"),
         btn("🌐 Set Webhook URL", "ai_set:webhook_url", style="primary")],
        [btn("🧪 Run Live Test", "ai_test", style="success")],
        [btn("🔙 Back", "admin_open", style="danger")],
    ]
    return text, kb(*rows)


def _guard(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


@router.callback_query(F.data == "admin_ai")
async def cb_ai(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("🔒 This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ai_prov:"))
async def cb_prov(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("🔒 This control is reserved for the super admin.", show_alert=True)
        return
    prov = call.data.split(":", 1)[1]
    if prov not in ("free", "anthropic", "off"):
        await call.answer("❌ That provider isn't recognised — pick one from the panel.", show_alert=True)
        return
    await set_ai_config("provider", prov)
    await call.answer(f"✨ Provider switched to {_PROV_LABEL.get(prov, prov)} — live now.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "ai_reset_url")
async def cb_reset_url(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("🔒 This control is reserved for the super admin.", show_alert=True)
        return
    await set_ai_config("free_url", DEFAULT_FREE_URL)
    await call.answer("♻️ Free API URL restored to the default endpoint.")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "ai_webhook_toggle")
async def cb_webhook_toggle(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("🔒 This control is reserved for the super admin.", show_alert=True)
        return
    cfg = await get_ai_config()
    new_state = not cfg["webhook_enabled"]
    if new_state and not cfg["webhook_url"]:
        await call.answer("🌐 Set a Webhook URL first, then turn webhook mode on.", show_alert=True)
        return
    await set_ai_config("webhook_enabled", new_state)
    await call.answer("🪝 Webhook mode " + ("ON — every AI request now routes to your webhook."
                                            if new_state else "OFF — back to the selected provider."))
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


_PROMPTS = {
    "free_url": "🔗 <b>Set Free API Endpoint</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>"
                "Send the new <b>base URL</b> for the free provider, for example:\n"
                "<code>https://bots.lt/Apis/AI/gpt.php</code>\n\n"
                "We'll call it as <code>URL?message=...</code> whenever AI is requested."
                "</blockquote>\n"
                "<i>💡 Tap Cancel below to keep the current endpoint.</i>",
    "key": "🔑 <b>Set Claude API Key</b>\n"
           "━━━━━━━━━━━━━━━━━━━━\n"
           "<blockquote>"
           "Paste your Anthropic key (it begins with <code>sk-ant-</code>).\n\n"
           "🛡 For your security we'll delete the message the moment it's saved."
           "</blockquote>\n"
           "<i>💡 Tap Cancel below to keep the current key.</i>",
    "model": "🧠 <b>Set Claude Model</b>\n"
             "━━━━━━━━━━━━━━━━━━━━\n"
             "<blockquote>"
             "Send the <b>model id</b> you'd like to run, for example:\n"
             "<code>claude-haiku-4-5-20251001</code>"
             "</blockquote>\n"
             "<i>💡 Tap Cancel below to keep the current model.</i>",
    "webhook_url": "🌐 <b>Set AI Webhook URL</b>\n"
                   "━━━━━━━━━━━━━━━━━━━━\n"
                   "<blockquote>"
                   "Send the full <b>webhook endpoint</b> for your custom AI backend, e.g.\n"
                   "<code>https://your-api.example.com/ai</code>\n\n"
                   "We'll POST <code>{\"message\": …, \"prompt\": …}</code> there and read "
                   "the reply from the response. Turn 🪝 <b>Webhook</b> ON to use it."
                   "</blockquote>\n"
                   "<i>💡 Tap Cancel below to keep the current URL.</i>",
}
_STATE = {"free_url": AIFSM.free_url, "key": AIFSM.key, "model": AIFSM.model,
          "webhook_url": AIFSM.webhook_url}


@router.callback_query(F.data.startswith("ai_set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    if not _guard(call.from_user.id):
        await call.answer("🔒 This control is reserved for the super admin.", show_alert=True)
        return
    field = call.data.split(":", 1)[1]
    if field not in _STATE:
        await call.answer("❌ That setting isn't recognised — pick one from the panel.", show_alert=True)
        return
    await call.answer()
    await state.set_state(_STATE[field])
    await call.message.answer(_PROMPTS[field], reply_markup=kb(cancel_row("admin_ai")))


async def _save_field(message: Message, state: FSMContext, cfg_key: str, friendly: str) -> None:
    raw = (message.text or "").strip()
    await state.clear()
    if raw.lower() == "/cancel":
        await message.answer("❌ <b>No changes made.</b>\n<i>Your current setting stays exactly as it was.</i>",
                             reply_markup=kb([btn("🤖 Back to AI Engine", "admin_ai", style="primary")]))
        return
    await set_ai_config(cfg_key, raw)
    await message.answer(
        f"✨ <b>{friendly} updated</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Live across every AI feature this very moment — no redeploy needed.</i>",
        reply_markup=kb([btn("🤖 Back to AI Engine", "admin_ai", style="primary")]))


@router.message(AIFSM.free_url, F.text)
async def on_url(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "free_url", "Free API endpoint")


@router.message(AIFSM.key, F.text)
async def on_key(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "anthropic_key", "Claude API key")
    try:
        await message.delete()  # don't leave the key sitting in chat
    except Exception:  # noqa: BLE001
        pass


@router.message(AIFSM.model, F.text)
async def on_model(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "model", "Claude model")


@router.message(AIFSM.webhook_url, F.text)
async def on_webhook_url(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "webhook_url", "AI webhook URL")


@router.callback_query(F.data == "ai_test")
async def cb_test(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("🔒 This control is reserved for the super admin.", show_alert=True)
        return
    await call.answer("🧪 Pinging your AI provider…")
    out = await ai_complete("Reply with exactly: PONG", max_tokens=20)
    if out:
        await call.message.answer(
            "✅ <b>AI is live and responding</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"🔁 <b>Provider reply</b>\n<code>{out[:200]}</code>"
            "</blockquote>\n"
            "<i>💡 Recommendations, Summaries and tagging are all good to go.</i>",
            reply_markup=kb([btn("🤖 Back to AI Engine", "admin_ai", style="primary")]))
    else:
        await call.message.answer(
            "❌ <b>No response from the provider</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            "The test came back empty. A quick checklist:\n"
            "🔌 The right provider is selected\n"
            "🔗 The Free URL is reachable\n"
            "🔑 The Claude key and model are valid"
            "</blockquote>\n"
            "<i>Adjust a setting above, then run the test again.</i>",
            reply_markup=kb([btn("🤖 Back to AI Engine", "admin_ai", style="primary")]))
