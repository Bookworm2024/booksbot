"""
utils/flags.py — feature flags (Mongo kv `flags`).

A feature is ON unless an admin has explicitly switched it off. Gate a feature's
entry point with `await is_on("<key>")`. Only list keys here that are actually
wired to a gate, so toggling always has a visible effect.
"""
from database.connection import MongoManager

# key → human label (shown in the 🚩 Feature Flags panel)
FLAGS = {
    "games":     "🎮 Games",
    "recommend": "🤖 AI Recommendations",
    "summaries": "📝 AI Summaries",
    "search":    "🔎 Archive Search",
}


async def all_flags() -> dict:
    db = await MongoManager.get()
    return await db.kv_get("flags", {}) or {}


async def is_on(name: str) -> bool:
    return bool((await all_flags()).get(name, True))   # default ON


async def set_flag(name: str, on: bool) -> None:
    db = await MongoManager.get()
    flags = await all_flags()
    flags[name] = bool(on)
    await db.kv_set("flags", flags)
