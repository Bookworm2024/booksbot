"""
utils/i18n.py — per-user language (i18n foundation).

A per-user language preference plus a tiny translation table for the highest-
visibility strings (greeting, common labels). t() falls back to English for any
missing key/language so partial coverage is always safe. RTL languages are
flagged so callers can adjust layout if needed.

Deeper UI coverage can grow by adding keys to STRINGS — handlers call
``t(key, lang)`` instead of hard-coding English.
"""
from database.connection import MongoManager

LANGUAGES = {
    "en": "🇬🇧 English",
    "es": "🇪🇸 Español",
    "hi": "🇮🇳 हिन्दी",
    "pt": "🇧🇷 Português",
    "ru": "🇷🇺 Русский",
    "ar": "🇸🇦 العربية",
}
RTL = {"ar"}

STRINGS: dict[str, dict[str, str]] = {
    "welcome": {
        "en": "Welcome back", "es": "Bienvenido de nuevo", "hi": "वापस स्वागत है",
        "pt": "Bem-vindo de volta", "ru": "С возвращением", "ar": "مرحبًا بعودتك",
    },
    "ready": {
        "en": "Your library is ready when you are.",
        "es": "Tu compañero de lectura está listo.",
        "hi": "आपका पठन साथी तैयार है।",
        "pt": "Seu companheiro de leitura está pronto.",
        "ru": "Ваш книжный помощник готов.",
        "ar": "رفيق القراءة الخاص بك جاهز.",
    },
    "lang_set": {
        "en": "Language saved", "es": "Idioma actualizado", "hi": "भाषा अपडेट हुई",
        "pt": "Idioma atualizado", "ru": "Язык обновлён", "ar": "تم تحديث اللغة",
    },
    "pick_lang": {
        "en": "Choose your language", "es": "Elige tu idioma", "hi": "अपनी भाषा चुनें",
        "pt": "Escolha seu idioma", "ru": "Выберите язык", "ar": "اختر لغتك",
    },
}

DEFAULT = "en"


def t(key: str, lang: str = DEFAULT) -> str:
    row = STRINGS.get(key, {})
    return row.get(lang) or row.get(DEFAULT) or key


def is_rtl(lang: str) -> bool:
    return lang in RTL


async def get_lang(uid: int) -> str:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"lang": 1})
    lang = (doc or {}).get("lang")
    return lang if lang in LANGUAGES else DEFAULT


async def set_lang(uid: int, code: str) -> None:
    if code not in LANGUAGES:
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid}, {"$set": {"lang": code}})
