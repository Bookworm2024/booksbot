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

## Phase 3 — Economy & wallet ✅
- ✅ BGM (permanent) / BCN (daily, expiring) wallet on Mongo
- ✅ /claim daily (random 3–5 BCN), /balance (with request counts)
- ✅ Redeem codes: /create (admin) + /redeem (per-user one-time, limited supply)
- ✅ BCN→BGM convert (≥50 BGM, 10×/month, 25% tax)
- ⬜ Payments: UPI (manual verify) + crypto (Oxapay) → BGM  → Phase 6

## Phase 4 — Requests ✅
- ✅ Auto request (search archive — Phase 2) + manual request (ebook/audiobook flow)
- ✅ Admin queue (/requests + panel), send-file (+archive enrich), mark-completed, cancel+reason+refund
- ✅ Refund: BCN→25%, BGM→75%, always paid in BGM
- ✅ Request tracking (/track, own-only) + history; admin /track_request

## Phase 5 — Mini Apps ⬜
- ⬜ eBook reader Mini App (PDF/EPUB, pagination, bookmarks, page memory)
- ⬜ Audiobook player Mini App (seek, speed, resume position)
- ⬜ Games as Mini Apps: Quiz, True/False, + new ones (leaderboards)

## Phase 6 — Admin & growth 🔜
- ✅ Referrals (+0.5 / +0.25 BGM, paid on join-gate clear) + leaderboard
- ✅ Support inbox (user → admins, one-tap admin reply)
- ✅ Ratings (/rate, 3/day, logged to admins)
- ✅ Global /stats analytics
- ⬜ AI recommendations (needs LLM API)
- ⬜ Broadcast engine (audience, progress, pause/resume)
- ⬜ Payments: UPI (manual) + crypto (Oxapay)
- ⬜ Admin dashboard Mini App, public logs / invite link

## Cross-cutting (applied throughout)
- Coloured keyboards everywhere · Mini Apps where they beat chat UI
- Mongo-backed (no in-memory state lost on restart) · idempotent flows
- Structured logging to a log channel · graceful error handling
