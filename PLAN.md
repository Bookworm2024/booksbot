# BooksBot — Rebuild Roadmap

Migrating `@getfreebooksbot` from TeleBotCreator (BJS) to a real aiogram +
MongoDB app on Koyeb, with Mini Apps. The original "200 improvements" ask is
captured as concrete work items grouped by phase. We ship phase-by-phase and
commit to `main`.

Legend: ✅ done · 🔜 next · ⬜ planned

## Phase 1 — Foundation ✅
- ✅ Project scaffold (aiogram 3.25, motor/Mongo, aiohttp, Telethon)
- ✅ Config via env + defensive parsing + startup validation
- ✅ Mongo manager: multi-cluster failover, indexes, kv store
- ✅ Coloured-keyboard helper (single source of truth) + vanilla-API fallback
- ✅ Onboarding: private check → join-gate → coloured dashboard
- ✅ Submenus: Library / Account / Tools (no dead-ends)
- ✅ Admin centre entry (super vs normal) + ban/unban + ban middleware
- ✅ Mini-App host (`/app/*`) + `/health`
- ✅ Telethon backfill scaffold (generate_session + backfill)
- ✅ Dockerfile / Koyeb-ready / README

## Phase 2 — File index & search 🔜
- 🔜 Run backfill; index 30k files (name, ext, kind, channel msg_id)
- 🔜 Live indexer: new channel posts → `files` (bot-side, real-time)
- 🔜 Fast search (Mongo text + prefix), paginated coloured results
- 🔜 Delivery via `bot.copy_message` from the file channel (valid bot file_ids)
- 🔜 Watchlist: notify users when a missing title is later added
- 🔜 Token cost per download (BCN/BGM) + refund on delivery failure

## Phase 3 — Economy & wallet ⬜
- ⬜ BGM (permanent) / BCN (daily, expiring) wallet on Mongo
- ⬜ /claim daily, /balance, BCN→BGM convert
- ⬜ Payments: UPI (manual verify) + crypto (Oxapay) → BGM
- ⬜ Redeem codes (create/claim, per-user one-time)

## Phase 4 — Requests ⬜
- ⬜ Auto request (search archive) + manual request (admin fulfilment)
- ⬜ Admin request queue, mark-completed, send-file, cancel-with-reason
- ⬜ Request tracking + history, favorites

## Phase 5 — Mini Apps ⬜
- ⬜ eBook reader Mini App (PDF/EPUB, pagination, bookmarks, page memory)
- ⬜ Audiobook player Mini App (seek, speed, resume position)
- ⬜ Games as Mini Apps: Quiz, True/False, + new ones (leaderboards)

## Phase 6 — Admin & growth ⬜
- ⬜ Full admin dashboard Mini App (users, broadcast, stats, config)
- ⬜ Broadcast engine (audience, progress, pause/resume)
- ⬜ AI recommendations, referrals, ratings, support inbox
- ⬜ Analytics / stats dashboard

## Cross-cutting (applied throughout)
- Coloured keyboards everywhere · Mini Apps where they beat chat UI
- Mongo-backed (no in-memory state lost on restart) · idempotent flows
- Structured logging to a log channel · graceful error handling
