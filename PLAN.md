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
- ✅ **Secure Payment Portal** Mini App (pay.html): UPI QR + in-app UTR/FMPIB
  submit (/api/pay/ipaid) + live status polling (/api/pay/status) + cancel; also
  fronts the OxaPay crypto checkout. The chat UTR flow stays as a no-HTTPS
  fallback. Mirrors the inflowads unified payment portal, adapted to BGM.
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
  pay ₹ to UPI ID → submit UTR (in the Payment Portal Mini App, or chat) → IMAP
  monitor reads the FamPay credit email, matches UTR + exact amount (±₹2) →
  auto-credits BGM. Ledger handles emails that arrive before/after the UTR.
  Atomic single-credit via _confirm_payment. **Shares the FamPay inbox + UPI ID
  with the inflowads bot:** both poll the same Gmail; whichever bot the UTR was
  submitted to finds the order in its own (separate) DB and credits — no
  cross-bot double-credit (the other bot just parks the email in its ledger).
- ✅ Payments — crypto (**OxaPay**): pick a USD pack → USD-priced invoice (pay
  page offers every coin you've enabled) → HMAC-signed /oxapay-webhook
  auto-credits BGM (activates when OXAPAY_MERCHANT_API_KEY set)
- ✅ AI recommendations — Claude-backed, 100 titles/20-batch, refund on invalid
  genre (activates when ANTHROPIC_API_KEY set)
- ⬜ Admin dashboard Mini App (optional future polish)

## Hardening pass (post-audit)
- ✅ Mongo client `tz_aware=True` — fixes naive/aware datetime crash across
  balance/claim/downloads/games/captcha/invite (was the one critical bug)
- ✅ Atomic `find_one_and_update_global` → race-safe: OxaPay webhook credit,
  redeem codes (+ unique (code,user_id) index), BCN→BGM convert
- ✅ Search result cap + sort-key projection; watchlist `matched:False` filter
- ✅ Unique indexes: codes.code, code_claims(code,user_id), crypto_orders.order_id

## Phase 7 — Freemium model ✅ (replaces the token economy as the access model)
- ✅ Free vs **Premium** tier (`utils/premium.py` over vip_tier/vip_until; 👑 tier 1).
  Premium = ₹280 / $3 per 30d (from wallet) OR **1000 BGM → 7d** (grind path).
- ✅ 24h per-user **quotas** (`utils/quota.py`, admin-tunable, race-safe): request
  bot 2/∞ + paid overage; admin requests 1/3 ebook, 0/3 audiobook; AI search 2/5;
  summary 1/5; each game 2/5. Similar/By-mood + New Arrivals/Series/Challenges = Premium.
- ✅ Real-money **wallet** (`wallet_inr`/`wallet_usd`) topped up by repurposed UPI
  (FamPay) + crypto (OxaPay); buys Premium and the per-file overage (₹100/$2 → `dlpay:`).
- ✅ BGM kept as the earnable reward currency (games/referrals/quests/etc.); only sink
  now is the Premium exchange. **BCN retired** from UX (daily claim → BGM; converter
  removed; crates drop BGM). Every upsell → `go_premium`.
- ✅ Fixes: Challenges removed from My Library; junk archive entries (the "Game"
  failed-delivery card) filtered from For You/Discover via `utils/files.is_bookish`.

## Phase 8 — Auto-harvest (public-domain archive) ✅
- ✅ Background harvester (`utils/harvester.py`) pulls latest arrivals + back-fills
  books not yet in the DB from **public-domain** sources — Project Gutenberg
  (Gutendex JSON API) + Standard Ebooks (atom new-releases feed). Pluggable source
  layer for adding more **legal** feeds later.
- ✅ Light + capped: one file/tick, paced (`harvest_interval_sec` ~75s), hard
  weekly cap (`harvest_weekly_cap` 10k), idles when caught up. Dedupes by source
  id AND normalized title (never duplicates the legacy archive).
- ✅ Downloads best format (epub→mobi→txt, size-capped) → uploads to the file
  channel via `send_document` → indexes via the existing `extract_from_message`/
  `index_file` so search + `dl:` delivery + watchlist auto-notify all just work.
- ✅ Genre free from Gutenberg subjects, AI fallback only when unclear.
- ✅ Weekly admin digest (7-day timer); 🧰 More Tools → 📚 Harvester panel
  (status/toggle/report-now); cap/pace/size tunable in ⚙️ Live Pricing.
- ⛔ Scope note: **public-domain / legally-redistributable sources ONLY**. No
  shadow-library/piracy sources and no auto-scraping of in-copyright releases —
  recent in-copyright titles come via the operator (manual request fulfilment,
  forward-import, Telethon backfill), never an automated piracy pipeline.

## Phase 9 — File preparation & branding (the "renamer" layer) ✅
- ✅ Central `utils/prepare.py` — EVERY file the bot hands a user routes through it
  (search/For You/Discover taps, Favorites, Finished, admin manual sends) + all
  file-button labels.
- ✅ Clean names: messy archive filenames ("OceanofPDF_Atomic_Habits_") → "Atomic
  Habits" via a regex cleaner refined by the AI engine (batched, cached on the
  files doc as `clean_name`, used for buttons AND captions). Search/list shows a
  "🔄 Preparing your results…" card while first-seen titles are tidied.
- ✅ Branded caption: "<b>Clean Title</b>  @handle" (admin-set kv `brand_handle`,
  default @bookslibraryofficial).
- ✅ Cover thumbnail: the admin branding image (set in 🎨 Branding) is processed
  (Pillow → ≤320px/≤200KB JPEG) and baked onto each delivered document as its
  thumbnail with a clean filename — done once (re-uploaded to the file channel,
  coords cached `prepared_msg_id`/`prepared_file_id`), then delivered by copy.
  A "📤 Preparing your file…" message covers the one-time latency.
- ✅ Constraint-aware + flawless-degrading: cover applies to re-uploadable docs
  (≤20MB Bot-API download cap; legacy/Telethon bytes fetched via a one-time
  staging copy); larger/audio/un-fetchable files still get clean-name + branded
  caption. Prep failure never fails delivery (falls back) and never burns quota.
  Prepared/staging re-uploads carry an invisible marker the indexer skips (no dupes).
- ✅ Harvester bakes the cover + clean_name at ingest (it re-uploads anyway).
- ✅ Admin: 🧰 More Tools → 🎨 Branding (cover image, handle, on/off, cover on/off).

## Phase 10 — Request Arena (group-topic requests, free-tier entry) ✅
- ✅ Direct in-bot requesting (Request Bot + Concierge) is now PREMIUM-ONLY; free
  members are redirected to the public Request Arena group. (request.py /
  requests_manual.py gates → arena link + Go Premium.)
- ✅ `handlers/arena.py` listens to ONE configured group + forum topic (kv
  arena_chat/arena_topic, default @free_novellas / topic 33; admin panel 📣 Request
  Arena). Every text there is treated as a request (per-user cooldown, ignores
  bots/commands).
- ✅ Match → topic reply "Found N" + a deep-link that, in DM, runs the join/force-sub
  gate then delivers (premium unlimited; free daily quota → per-file overage/Premium
  upsell shown in the topic when the limit's used up). Single match auto-delivers;
  many → a pick list. "Buy this file" deep-link → wallet overage charge.
- ✅ No match → 🔔 Notify me (watchlist auto-DM when added; if the user never started
  the bot, a Start deep-link subscribes them + shows the dashboard + a confirm) and
  👤 Request from admins (concierge prefilled with the title; ebook quota + audiobook-
  premium rules still apply).
- ✅ Reuse, not duplication: tickets (arena_tickets, TTL 7d) + deep-links route
  through the EXISTING gate (start._not_joined), delivery/quota/overage
  (request.fulfil_download / fulfil_paid), watchlist (request._add_watchlist) and
  concierge (requests_manual.begin_concierge). Branding/prep applies to delivered files.
- ⚙️ Operational: the bot must be a group ADMIN (or have BotFather privacy mode OFF)
  to read the topic's messages.

## Status: feature-complete + hardened
All TBC features rebuilt + modernized; crypto via OxaPay.
Credential-gated features (AI, crypto) activate once their keys are set in the
host env. Remaining work is operational: deploy to Koyeb, run the Telethon
backfill, go live.

## Cross-cutting (applied throughout)
- Coloured keyboards everywhere · Mini Apps where they beat chat UI
- Mongo-backed (no in-memory state lost on restart) · idempotent flows
- Structured logging to a log channel · graceful error handling
