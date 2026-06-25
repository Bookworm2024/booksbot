"""
utils/currency.py — currency localization for price display.

A per-user display currency + a static USD-based rate table so users can see BGM
prices in a familiar currency. Display only — actual charges still go through the
real UPI (₹) and crypto ($) rails; this never touches the wallet.
"""
from database.connection import MongoManager

# code → (symbol, units per 1 USD). Approximate, display-only; admins/users
# treat these as a guide, not a live FX quote.
CURRENCIES: dict[str, tuple[str, float]] = {
    "USD": ("$", 1.0),
    "EUR": ("€", 0.92),
    "GBP": ("£", 0.79),
    "INR": ("₹", 83.0),
    "BRL": ("R$", 5.1),
    "NGN": ("₦", 1550.0),
    "PKR": ("₨", 278.0),
    "PHP": ("₱", 58.0),
    "IDR": ("Rp", 16000.0),
}

DEFAULT = "USD"


async def get_currency(uid: int) -> str:
    db = await MongoManager.get()
    doc = await db.find_one_global("users", {"user_id": uid}, {"currency": 1})
    code = (doc or {}).get("currency")
    return code if code in CURRENCIES else DEFAULT


async def set_currency(uid: int, code: str) -> None:
    if code not in CURRENCIES:
        return
    db = await MongoManager.get()
    await db.safe_update("users", {"user_id": uid}, {"$set": {"currency": code}})


def convert(usd: float, code: str) -> float:
    _sym, rate = CURRENCIES.get(code, CURRENCIES[DEFAULT])
    return float(usd) * rate


def fmt(usd: float, code: str) -> str:
    from utils.format import fmt_amount
    sym, _rate = CURRENCIES.get(code, CURRENCIES[DEFAULT])
    val = convert(usd, code)
    # whole units for high-magnitude currencies, 2 decimals otherwise
    decimals = 0 if val >= 100 else 2
    return f"{sym}{fmt_amount(val, decimals)}"
