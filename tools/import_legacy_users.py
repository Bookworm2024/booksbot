"""
tools/import_legacy_users.py — one-off importer for legacy bot users.

The old @getfreebooksbot ran on TeleBotCreator (bot id 254675630). Its user list
was exported as a JSON array:

    [{"user_id": "100007678", "name": "", "username": "",
      "language_code": "en", "creation_date": "2025-08-22 13:11:22",
      "last_active_date": "2025-08-23 07:59:47"}, ...]

This seeds those users into the Mongo `users` collection so that broadcasts,
comeback reminders, stats and every other audience-wide feature reach them too
(the broadcast/reminder workers iterate `users`, so a user only needs a row
there to be targeted).

IDEMPOTENT and safe to re-run: a user is created only if no cluster already has
them. A user who has already interacted with the NEW bot keeps their balances,
streaks and everything else untouched — we never overwrite an existing row.

Imported rows get the same default economy fields a fresh /start would create
(0 BGM / 0 BCN, not banned), with `is_new=False` so they are NOT re-announced as
new users, plus provenance markers `imported=True` / `import_source` so the
backfill is traceable and reversible.

Usage:
    # validate the file & preview what would happen — no DB, no env needed
    python tools/import_legacy_users.py path/to/bot_254675630_users.json --dry-run

    # import for real (reads MONGO_URL / MONGO_URLS from env or a local .env)
    python tools/import_legacy_users.py path/to/bot_254675630_users.json

Run from the repo root so `config` / `database` import cleanly (same as the
other tools). The JSON file itself is user PII — keep it out of git.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Allow running as `python tools/import_legacy_users.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("import_legacy_users")

IMPORT_SOURCE = "bot_254675630"
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: Any) -> Optional[datetime]:
    """Parse an exported 'YYYY-MM-DD HH:MM:SS' string as tz-aware UTC.

    The Mongo client runs tz_aware=True, so every datetime we store MUST be
    tz-aware or later aware/naive subtractions (balance/claim/games) crash.
    Returns None when the value is blank or unparseable.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _build_doc(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map one exported record to a fresh `users` document, or None if the
    user_id is missing/invalid (the only field we truly require)."""
    # A JSON array may legally contain non-object elements (a stray null/string/
    # number); treat anything that isn't a dict as an invalid row instead of
    # crashing the whole import on `rec.get(...)`.
    if not isinstance(rec, dict):
        return None
    try:
        uid = int(str(rec.get("user_id", "")).strip())
    except (ValueError, TypeError):
        return None
    if uid <= 0:
        return None

    joined = _parse_dt(rec.get("creation_date")) or _now()
    last_active = _parse_dt(rec.get("last_active_date")) or joined

    return {
        "user_id": uid,
        "first_name": (rec.get("name") or "").strip(),
        "username": (rec.get("username") or "").strip().lstrip("@"),
        "language_code": (rec.get("language_code") or "").strip(),
        "is_banned": False,
        "joined_at": joined,
        "last_active": last_active,
        "bookgem": 0.0,
        "bookcoin": 0.0,
        "bcn_claimed_at": None,
        "referrer": None,
        # NOT a new user — don't trigger the "🆕 New User" log on first /start.
        "is_new": False,
        # provenance for a traceable / reversible backfill
        "imported": True,
        "import_source": IMPORT_SOURCE,
        "imported_at": _now(),
    }


def _load_records(path: str) -> list[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"Expected a JSON array of users, got {type(data).__name__}.")
    return data


def _summarize(records: list[Dict[str, Any]]) -> tuple[Dict[int, Dict[str, Any]], int]:
    """Build {uid: doc} (last-wins on dup ids within the file) + invalid count."""
    docs: Dict[int, Dict[str, Any]] = {}
    invalid = 0
    for rec in records:
        doc = _build_doc(rec)
        if doc is None:
            invalid += 1
            continue
        docs[doc["user_id"]] = doc
    return docs, invalid


def _mask_uri(uri: str) -> str:
    """Hide credentials in a Mongo URI before logging it."""
    if "@" in uri and "://" in uri:
        scheme, rest = uri.split("://", 1)
        if "@" in rest:
            return f"{scheme}://***:***@{rest.split('@', 1)[1]}"
    return uri


async def _import(path: str) -> None:
    from config import MONGO_DB_NAME, MONGO_URLS
    from database.connection import MongoManager

    if not MONGO_URLS:
        raise SystemExit(
            "No MongoDB URL in the environment. Set MONGO_URL (or MONGO_URLS) — "
            "e.g. the same connection string the bot uses on Koyeb — then re-run.\n"
            'PowerShell:  $env:MONGO_URL="mongodb+srv://..."; '
            "python tools/import_legacy_users.py <file>"
        )

    records = _load_records(path)
    docs, invalid = _summarize(records)
    logger.info(
        "Loaded %d rows -> %d unique valid users (%d invalid/blank ids) from %s",
        len(records), len(docs), invalid, path,
    )
    logger.info("Target DB: %s  via  %s", MONGO_DB_NAME, _mask_uri(MONGO_URLS[0]))

    db = await MongoManager.get()

    inserted = existed = errors = 0
    processed = 0
    for uid, doc in docs.items():
        processed += 1
        try:
            # find_one_global checks every cluster, so we never create a
            # cross-cluster duplicate; we only insert when nobody has the user.
            existing = await db.find_one_global("users", {"user_id": uid}, {"_id": 1})
            if existing:
                existed += 1
            elif await db.safe_insert("users", doc):
                inserted += 1
            else:
                existed += 1  # lost a race / unique-index dupe — already present
        except Exception as exc:  # noqa: BLE001 — keep going, report at the end
            errors += 1
            logger.warning("user %s failed: %s", uid, exc)
        if processed % 500 == 0:
            logger.info("  ...%d/%d processed (+%d new)", processed, len(docs), inserted)

    total = await db.count_global("users")
    logger.info(
        "\nDone. inserted=%d  already_present=%d  errors=%d  invalid_rows=%d\n"
        "`users` collection now holds %d total.",
        inserted, existed, errors, invalid, total,
    )


def _dry_run(path: str) -> None:
    records = _load_records(path)
    docs, invalid = _summarize(records)
    logger.info(
        "DRY RUN - no database touched.\n"
        "Loaded %d rows -> %d unique valid users (%d invalid/blank ids).",
        len(records), len(docs), invalid,
    )
    dup = len(records) - invalid - len(docs)
    if dup:
        logger.info("(%d duplicate user_ids within the file collapsed to one each.)", dup)
    sample = list(docs.values())[:3]
    for d in sample:
        logger.info(
            "  sample: id=%s name=%r lang=%s joined=%s last_active=%s",
            d["user_id"], d["first_name"], d["language_code"],
            d["joined_at"].isoformat(), d["last_active"].isoformat(),
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Import legacy TBC users into Mongo `users`.")
    ap.add_argument("json_path", help="Path to the exported users JSON array.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate & preview only; do not connect to Mongo.")
    args = ap.parse_args()

    if not os.path.isfile(args.json_path):
        raise SystemExit(f"File not found: {args.json_path}")

    if args.dry_run:
        _dry_run(args.json_path)
    else:
        asyncio.run(_import(args.json_path))


if __name__ == "__main__":
    main()
