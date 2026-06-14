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

## Phase 5 — Mini Apps 🔜
- ✅ Quiz + True/False as **Mini Apps with server-side scoring** (answers never
  sent to client; sessions single-use; daily limits + HMAC initData auth)
- ✅ Telegram initData HMAC validation (utils/webapp_auth.py)
- ✅ Starter question bank auto-seed + game API (/api/game/new, /api/game/submit)
- ✅ eBook reader Mini App (PDF via pdf.js, EPUB via epub.js, pagination,
  bookmarks, page/CFI memory) — reader.html + /api/file + /api/reader/state
- ✅ Audiobook player Mini App (seek, ±15s, 0.75–2× speed, resume position)
- ✅ File-stream endpoint gated to the user's Favorites (initData-auth);
  graceful >20MB / no-file_id fallbacks
- ⬜ Admin question management + leaderboards

## Phase 6 — Admin & growth 🔜
- ✅ Referrals (+0.5 / +0.25 BGM, paid on join-gate clear) + leaderboard
- ✅ Support inbox (user → admins, one-tap admin reply)
- ✅ Ratings (/rate, 3/day, logged to admins)
- ✅ Global /stats analytics
- ✅ Broadcast engine (audience, live progress, pause/resume/stop)
- ✅ Admin question manager (add quiz/TF, counts)
- ✅ Public logs / invite link (/get_link, 24h single-use, 1/day)
- ✅ Safe in-house captcha (CAPTCHA_ENABLED) replacing 3rd-party verification
- ✅ Payments — UPI **email auto-verified** (ported from inflowads): pick BGM →
  pay ₹ to UPI ID → submit UTR → IMAP monitor reads the FamPay credit email,
  matches UTR + exact amount (±₹2) → auto-credits BGM. Ledger handles emails
  that arrive before/after the UTR. Atomic single-credit via _confirm_payment.
- ✅ Payments — crypto (**Heleket**, same gateway as inflowads): coin/network →
  USD pack (≥$5 min) → invoice → signed /heleket-webhook auto-credits BGM
  (activates when HELEKET_API_KEY + HELEKET_MERCHANT_ID set)
- ✅ AI recommendations — Claude-backed, 100 titles/20-batch, refund on invalid
  genre (activates when ANTHROPIC_API_KEY set)
- ⬜ Admin dashboard Mini App (optional future polish)

## Hardening pass (post-audit)
- ✅ Mongo client `tz_aware=True` — fixes naive/aware datetime crash across
  balance/claim/downloads/games/captcha/invite (was the one critical bug)
- ✅ Atomic `find_one_and_update_global` → race-safe: Heleket webhook credit,
  redeem codes (+ unique (code,user_id) index), BCN→BGM convert
- ✅ Search result cap + sort-key projection; watchlist `matched:False` filter
- ✅ Unique indexes: codes.code, code_claims(code,user_id), crypto_orders.order_id

## Status: feature-complete + hardened
All TBC features rebuilt + modernized; crypto via Heleket like inflowads.
Credential-gated features (AI, crypto) activate once their keys are set in the
host env. Remaining work is operational: deploy to Koyeb, run the Telethon
backfill, go live.

## Cross-cutting (applied throughout)
- Coloured keyboards everywhere · Mini Apps where they beat chat UI
- Mongo-backed (no in-memory state lost on restart) · idempotent flows
- Structured logging to a log channel · graceful error handling
