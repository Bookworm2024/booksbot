"""
database/connection.py — MongoDB manager for BooksBot.

A pragmatic single-/multi-cluster manager:
  • Connects to every MONGO_URLS entry; writes go to the first healthy one.
  • If a write fails with a quota/space OperationFailure, it fails over to the
    next cluster (Atlas free tiers cap at 512 MB — the file index can outgrow
    one cluster). Reads fan out across all connected clusters.
  • Async-only (motor). Indexes are created once at startup.

This is intentionally lighter than the inflowads 5-cluster waterfall; it keeps
the same safe_insert / safe_update / find_one_global surface so handlers don't
care how many clusters exist.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.errors import DuplicateKeyError, OperationFailure

from config import MONGO_DB_NAME, MONGO_URLS

logger = logging.getLogger(__name__)

# Substrings that mean "this cluster is full" → fail over to the next one.
_QUOTA_HINTS = ("quota", "space", "limit", "over the", "atlas", "you are over")


class MongoManager:
    _instance: Optional["MongoManager"] = None
    _lock = asyncio.Lock()

    def __init__(self) -> None:
        self.clients: Dict[int, AsyncIOMotorClient] = {}
        self.dbs: Dict[int, AsyncIOMotorDatabase] = {}
        self.healthy: List[int] = []
        self.write_idx: int = 0

    # ── singleton ────────────────────────────────────────────────────────────
    @classmethod
    async def get(cls) -> "MongoManager":
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                await cls._instance._init()
            return cls._instance

    async def _init(self) -> None:
        for idx, url in enumerate(MONGO_URLS):
            try:
                client = AsyncIOMotorClient(
                    url,
                    serverSelectionTimeoutMS=6000,
                    minPoolSize=2,
                    maxPoolSize=40,
                    maxIdleTimeMS=60000,
                    retryWrites=True,
                    # CRITICAL: without this, stored aware UTC datetimes read back
                    # naive → every aware-minus-stored subtraction (balance/claim/
                    # games/captcha/invite) raises TypeError. Keep tz-aware.
                    tz_aware=True,
                )
                await client.admin.command("ping")
                self.clients[idx] = client
                self.dbs[idx] = client[MONGO_DB_NAME]
                self.healthy.append(idx)
                logger.info("MongoDB cluster %d connected.", idx)
            except Exception as exc:  # noqa: BLE001 — log & continue to next
                logger.error("MongoDB cluster %d failed: %s", idx, exc)

        if not self.healthy:
            raise RuntimeError(
                "No MongoDB clusters reachable. Check MONGO_URL / MONGO_URLS."
            )
        self.write_idx = self.healthy[0]
        await self._create_indexes()

    # ── indexes ──────────────────────────────────────────────────────────────
    async def _create_indexes(self) -> None:
        for idx in self.healthy:
            db = self.dbs[idx]
            try:
                await db.users.create_index([("user_id", ASCENDING)], unique=True)
                await db.files.create_index([("file_unique_id", ASCENDING)], unique=True)
                # Text index powers fast title search across the file archive.
                await db.files.create_index([("name", TEXT)], default_language="english")
                await db.files.create_index([("name_lc", ASCENDING)])
                await db.files.create_index([("indexed_at", DESCENDING)])
                await db.files.create_index([("dl_count", DESCENDING)])
                await db.files.create_index([("featured_until", DESCENDING)])
                await db.requests.create_index([("request_id", ASCENDING)], unique=True)
                await db.requests.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
                await db.favorites.create_index([("user_id", ASCENDING), ("file_unique_id", ASCENDING)], unique=True)
                await db.kv.create_index([("k", ASCENDING)], unique=True)
                await db.codes.create_index([("code", ASCENDING)], unique=True)
                await db.code_claims.create_index([("code", ASCENDING), ("user_id", ASCENDING)], unique=True)
                await db.crypto_orders.create_index([("order_id", ASCENDING)], unique=True)
                # UPI email-monitor collections
                await db.payments.create_index([("order_id", ASCENDING)], unique=True)
                await db.payments.create_index([("submitted_utr", ASCENDING), ("status", ASCENDING)])
                await db.processed_emails.create_index([("uid", ASCENDING)], unique=True)
                await db.fampay_ledger.create_index([("utr", ASCENDING)])
                await db.raw_emails.create_index([("uid", ASCENDING)], unique=True)
                await db.reader_state.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
                await db.bookle_sessions.create_index([("uid", ASCENDING), ("day", ASCENDING)], unique=True)
                await db.users.create_index([("game_bgm", DESCENDING)])
                await db.users.create_index([("last_active", ASCENDING)])
                await db.users.create_index([("downloads", DESCENDING)])
                await db.users.create_index([("ref_count", DESCENDING)])
                await db.users.create_index([("login_streak", DESCENDING)])
            except OperationFailure as exc:
                # "already exists with different options" etc. are benign.
                logger.debug("Index note on cluster %d: %s", idx, exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("Index build failed on cluster %d: %s", idx, exc)

    # ── write surface (with failover) ──────────────────────────────────────────
    def _next_write(self) -> Optional[int]:
        order = [i for i in self.healthy if i != self.write_idx]
        return order[0] if order else None

    async def safe_insert(self, coll: str, doc: Dict[str, Any]) -> bool:
        try:
            await self.dbs[self.write_idx][coll].insert_one(doc)
            return True
        except DuplicateKeyError:
            return False
        except OperationFailure as exc:
            if any(h in str(exc).lower() for h in _QUOTA_HINTS):
                nxt = self._next_write()
                if nxt is not None:
                    logger.warning("Cluster %d full; failing writes over to %d.", self.write_idx, nxt)
                    self.write_idx = nxt
                    return await self.safe_insert(coll, doc)
            raise

    async def safe_update(self, coll: str, flt: Dict[str, Any], update: Dict[str, Any],
                          upsert: bool = True) -> None:
        # Update the doc wherever it already lives; otherwise upsert on the
        # active write cluster.
        for idx in self.healthy:
            existing = await self.dbs[idx][coll].find_one(flt, {"_id": 1})
            if existing:
                await self.dbs[idx][coll].update_one(flt, update)
                return
        if upsert:
            await self.dbs[self.write_idx][coll].update_one(flt, update, upsert=True)

    # ── read surface (fan-out) ─────────────────────────────────────────────────
    async def find_one_global(self, coll: str, flt: Dict[str, Any],
                              proj: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        for idx in self.healthy:
            doc = await self.dbs[idx][coll].find_one(flt, proj)
            if doc:
                return doc
        return None

    async def find_global(self, coll: str, flt: Dict[str, Any],
                          *, limit: int = 0, sort: Optional[list] = None,
                          proj: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx in self.healthy:
            cur = self.dbs[idx][coll].find(flt, proj)
            if sort:
                cur = cur.sort(sort)
            if limit:
                cur = cur.limit(limit)
            results.extend([d async for d in cur])
        if sort:
            key, direction = sort[0]
            results.sort(key=lambda d: d.get(key) or 0, reverse=(direction == DESCENDING))
        return results[:limit] if limit else results

    async def find_one_and_update_global(self, coll: str, flt: Dict[str, Any],
                                         update: Dict[str, Any], *,
                                         return_before: bool = False) -> Optional[Dict[str, Any]]:
        """Atomic conditional update used to make credit/decrement paths race-safe.
        Tries each cluster; returns the matched doc (pre- or post-update) or None
        if no cluster had a doc matching `flt`."""
        from pymongo import ReturnDocument
        rd = ReturnDocument.BEFORE if return_before else ReturnDocument.AFTER
        for idx in self.healthy:
            doc = await self.dbs[idx][coll].find_one_and_update(flt, update, return_document=rd)
            if doc:
                return doc
        return None

    async def count_global(self, coll: str, flt: Optional[Dict[str, Any]] = None) -> int:
        total = 0
        for idx in self.healthy:
            total += await self.dbs[idx][coll].count_documents(flt or {})
        return total

    # ── tiny key/value store (mirrors TBC Bot.getData/saveData) ─────────────────
    async def kv_get(self, key: str, default: Any = None) -> Any:
        doc = await self.find_one_global("kv", {"k": key})
        return doc.get("v") if doc else default

    async def kv_set(self, key: str, value: Any) -> None:
        await self.safe_update("kv", {"k": key}, {"$set": {"k": key, "v": value}})
