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

## Phase 2 — File index & search ✅
- ✅ Live indexer: new channel posts → `files` (real-time, bot-side)
- ✅ Telethon backfill scaffold for the 30k history (tools/backfill.py)
- ✅ All-words search, paginated coloured results
- ✅ Delivery via `bot.copy_message` from the file channel
- ✅ Watchlist: notify users when a missing title is later added
- ✅ Token cost per download (BCN-first) + refund on delivery failure
- ✅ Favorites: add / list / view (free re-deliver) / remove
- 🔜 (operational) run backfill once creds + FILE_CHANNEL_ID are set

## Phase 3 — Economy & wallet 🔜
- ✅ BGM (permanent) / BCN (daily, expiring) wallet on Mongo
- ✅ /claim daily (random 3–5 BCN), /balance
- ✅ Redeem codes: /create (admin) + /redeem (per-user one-time, limited supply)
- 🔜 BCN→BGM convert
- ⬜ Payments: UPI (manual verify) + crypto (Oxapay) → BGM

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
