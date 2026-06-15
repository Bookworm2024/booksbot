"""
tools/merge_gen_bank.py — merge the generated batch files into the question bank.

Reads every data/_gen/*.json batch (produced by the generation workflow),
normalises + de-dupes them (by the same qhash the bot uses), writes the
committed seed file data/questions_seed.json, and bulk-loads them into the live
Mongo `questions` collection (dedupe enforced by the unique qhash index).

Usage:
  python tools/merge_gen_bank.py                      # write seed + load to Mongo
  python tools/merge_gen_bank.py --no-load            # only (re)write the seed file
  (needs MONGO_URL in env unless --no-load)
"""
import argparse
import asyncio
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.games import _normalize_seed  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DIR = os.path.join(ROOT, "data", "_gen")
SEED_FILE = os.path.join(ROOT, "data", "questions_seed.json")


def collect() -> tuple[list, dict, int]:
    """Return (clean_docs, per_category_counts, dropped) from all batch files."""
    seen = set()
    docs = []
    by_cat = {}
    dropped = 0
    for path in sorted(glob.glob(os.path.join(GEN_DIR, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                arr = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! skip {os.path.basename(path)}: {exc}")
            continue
        for raw in arr if isinstance(arr, list) else []:
            norm = _normalize_seed(raw) if isinstance(raw, dict) else None
            if not norm:
                dropped += 1
                continue
            if norm["qhash"] in seen:
                continue
            seen.add(norm["qhash"])
            key = norm["game"] + ("/" + norm["level"] if norm.get("level") else "")
            by_cat[key] = by_cat.get(key, 0) + 1
            # store a clean source doc (qhash is recomputed on load)
            clean = {"game": norm["game"], "q": norm["q"]}
            if "level" in norm:
                clean["level"] = norm["level"]
            if "options" in norm:
                clean["options"] = norm["options"]
            clean["a"] = norm["a"]
            docs.append(clean)
    return docs, by_cat, dropped


async def load_to_mongo(docs: list) -> None:
    from database.connection import MongoManager
    from pymongo.errors import BulkWriteError
    db = await MongoManager.get()
    coll = db.dbs[db.write_idx]["questions"]
    loaded = 0
    batch = []
    for d in docs:
        n = _normalize_seed(d)
        if n:
            batch.append(n)
    for i in range(0, len(batch), 1000):
        chunk = batch[i:i + 1000]
        try:
            res = await coll.insert_many(chunk, ordered=False)
            loaded += len(res.inserted_ids)
        except BulkWriteError as bwe:
            loaded += bwe.details.get("nInserted", 0)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! insert chunk failed: {exc}")
    print(f"Inserted {loaded} new docs into Mongo (duplicates skipped by qhash index).")
    # live totals
    for g, lv in (("quiz", "beginner"), ("quiz", "moderate"), ("quiz", "advanced"),
                  ("tf", None), ("guess", None), ("firstline", None), ("author", None)):
        flt = {"game": g}
        if lv:
            flt["level"] = lv
        c = await db.count_global("questions", flt)
        print(f"  live {g + ('/' + lv if lv else ''):18s} = {c}")
    print(f"  live TOTAL              = {await db.count_global('questions')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-load", action="store_true", help="Only write the seed file.")
    args = ap.parse_args()

    docs, by_cat, dropped = collect()
    print(f"Collected {len(docs)} unique questions ({dropped} dropped as invalid).")
    for k in sorted(by_cat):
        print(f"  {k:18s} {by_cat[k]}")

    os.makedirs(os.path.dirname(SEED_FILE), exist_ok=True)
    with open(SEED_FILE, "w", encoding="utf-8") as fh:
        json.dump(docs, fh, ensure_ascii=False)
    print(f"Wrote seed file: {SEED_FILE} ({os.path.getsize(SEED_FILE)//1024} KB)")

    if not args.no_load:
        asyncio.run(load_to_mongo(docs))


if __name__ == "__main__":
    main()
