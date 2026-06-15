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
from utils.keyboards import btn, kb

logger = logging.getLogger(__name__)
router = Router()

_PROV_LABEL = {"free": "🆓 Free API (bots.lt)", "anthropic": "💎 Claude (Anthropic)", "off": "🚫 Off"}


class AIFSM(StatesGroup):
    free_url = State()
    key = State()
    model = State()


def _mask(s: str) -> str:
    if not s:
        return "—"
    return f"{s[:4]}…{s[-3:]}" if len(s) > 10 else "set"


async def _panel():
    cfg = await get_ai_config()
    prov = cfg["provider"]
    text = (
        "<b>🤖 AI Settings</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Provider: <b>{_PROV_LABEL.get(prov, prov)}</b>\n"
        f"🔗 Free URL: <code>{cfg['free_url']}</code>\n"
        f"🔑 Claude key: <code>{_mask(cfg['anthropic_key'])}</code>\n"
        f"🧠 Claude model: <code>{cfg['model']}</code>\n\n"
        "<i>Powers 🤖 Recommendations, 📝 Summaries and 🏷 genre tagging. "
        "The Free API needs no key.</i>"
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
        [btn("🧪 Test AI now", "ai_test", style="success")],
        [btn("🔙 Back", "admin_open", style="danger")],
    ]
    return text, kb(*rows)


def _guard(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


@router.callback_query(F.data == "admin_ai")
async def cb_ai(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer()
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ai_prov:"))
async def cb_prov(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    prov = call.data.split(":", 1)[1]
    if prov not in ("free", "anthropic", "off"):
        await call.answer("Unknown", show_alert=True)
        return
    await set_ai_config("provider", prov)
    await call.answer(f"Provider → {prov}")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "ai_reset_url")
async def cb_reset_url(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await set_ai_config("free_url", DEFAULT_FREE_URL)
    await call.answer("Reset to default")
    text, markup = await _panel()
    await call.message.edit_text(text, reply_markup=markup)


_PROMPTS = {
    "free_url": "🔗 Send the new <b>Free API base URL</b> (e.g. https://bots.lt/Apis/AI/gpt.php). "
                "It will be called as <code>URL?message=...</code>. /cancel to abort.",
    "key": "🔑 Send the <b>Claude API key</b> (sk-ant-…). /cancel to abort.",
    "model": "🧠 Send the <b>Claude model id</b> (e.g. claude-haiku-4-5-20251001). /cancel to abort.",
}
_STATE = {"free_url": AIFSM.free_url, "key": AIFSM.key, "model": AIFSM.model}


@router.callback_query(F.data.startswith("ai_set:"))
async def cb_set(call: CallbackQuery, state: FSMContext) -> None:
    if not _guard(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    field = call.data.split(":", 1)[1]
    if field not in _STATE:
        await call.answer("Unknown", show_alert=True)
        return
    await call.answer()
    await state.set_state(_STATE[field])
    await call.message.answer(_PROMPTS[field])


async def _save_field(message: Message, state: FSMContext, cfg_key: str, friendly: str) -> None:
    raw = (message.text or "").strip()
    await state.clear()
    if raw.lower() == "/cancel":
        await message.answer("❌ Cancelled.")
        return
    await set_ai_config(cfg_key, raw)
    await message.answer(f"✅ {friendly} updated. Applies immediately.",
                         reply_markup=kb([btn("🤖 AI Settings", "admin_ai", style="primary")]))


@router.message(AIFSM.free_url, F.text)
async def on_url(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "free_url", "Free API URL")


@router.message(AIFSM.key, F.text)
async def on_key(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "anthropic_key", "Claude key")
    try:
        await message.delete()  # don't leave the key sitting in chat
    except Exception:  # noqa: BLE001
        pass


@router.message(AIFSM.model, F.text)
async def on_model(message: Message, state: FSMContext) -> None:
    await _save_field(message, state, "model", "Claude model")


@router.callback_query(F.data == "ai_test")
async def cb_test(call: CallbackQuery) -> None:
    if not _guard(call.from_user.id):
        await call.answer("Super admin only", show_alert=True)
        return
    await call.answer("Testing…")
    out = await ai_complete("Reply with exactly: PONG", max_tokens=20)
    if out:
        await call.message.answer(f"✅ <b>AI is working.</b>\nReply: <code>{out[:200]}</code>",
                                  reply_markup=kb([btn("🤖 AI Settings", "admin_ai", style="primary")]))
    else:
        await call.message.answer("❌ <b>No response.</b> Check the provider/URL/key and try again.",
                                  reply_markup=kb([btn("🤖 AI Settings", "admin_ai", style="primary")]))
