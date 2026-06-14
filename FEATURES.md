# BooksBot — 500-Feature Roadmap

The vision: the most powerful books/audiobooks bot on Telegram — money-oriented
for admins, frictionless for users, with a universal reader and a deep games
layer. Built in tested batches; ✅ = shipped, 🔜 = next, ⬜ = planned.

Legend counts toward ~500 features across 14 pillars.

---

## 1. Universal Reader & Media (any file, type-aware UI)
- ✅ PDF reader (pdf.js): pagination, bookmarks, page memory
- ✅ EPUB reader (epub.js): CFI location memory, bookmarks
- ✅ Audiobook player: seek, ±15s, 0.75–2× speed, resume
- ✅ Universal viewer dispatcher (`view.html`) — routes by file type
- ✅ Video player (mp4/webm/mov): speed, resume, fullscreen
- ✅ Image viewer (jpg/png/gif/webp): tap-zoom
- ✅ Text/Markdown reader: font size, scroll memory
- ✅ CBZ comic reader (zip-of-images, paged) · ✅ reading themes (light/sepia/dark)
- ✅ Font size + line-spacing controls (persisted) · ✅ Continue-Reading shelf
- ✅ TTS read-aloud (PDF/EPUB/text, device voices) · ✅ audio sleep timer
- ✅ Reading streaks + stats (📊 My Reading: streak/days/in-progress/bookmarks)
- 🔜 MOBI/AZW3 support · CBR comic reader · font-family picker
- 🔜 Per-book notes & highlights · highlight export · "books finished" shelf
- ⬜ Sync position across devices · last-page push notification
- ⬜ Dictionary tap-lookup · translate selection · Wikipedia lookup
- ⬜ Adjustable playback EQ for audiobooks · chapter markers

## 2. Book Discovery & Content
- ✅ AI recommendations (genre → 100 titles)
- ✅ AI book summaries (overview, themes, takeaways)
- ✅ Discover hub: 🆕 New Arrivals · 🔥 Popular (download-ranked)
- ✅ Book of the Day (deterministic daily) · 💬 Daily literary Quote
- 🔜 "Similar to this" recommendations · mood-based picks
- 🔜 Author spotlight · series detector & next-in-series
- 🔜 Genre browse hub · curated collections / shelves · reading challenges
- ⬜ Goodreads-style ratings/reviews per title
- ⬜ Personal reading goals & yearly wrap-up
- ⬜ Wishlist/TBR list with reminders

## 3. Powerful Search
- ✅ All-words archive search, paginated
- ✅ Type filters (All / PDF / EPUB / Audio) in results
- ✅ Inline mode (`@bot query`) — search from any chat, deep-links into bot
- 🔜 Fuzzy/typo-tolerant search · author filter · sort options
- ⬜ Saved searches · search history · semantic (embedding) search

## 4. Games & Brain (mini apps, book/nerd themed)
- ✅ Quiz (3 levels, server-scored)
- ✅ True/False (server-scored)
- ✅ Guess-the-Book (from blurb) · ✅ First-Line quiz · ✅ Author Match
- ✅ "Bookle" — Wordle-style daily word game (server-scored, shared daily word)
- ✅ Games leaderboard (top earners across all games)
- 🔜 Cover-Guess · Title Anagram/Word-Builder · Speed-Reading WPM · Memory match
- 🔜 Literary Hangman · Crossword · daily challenge · weekly tournament
- 🔜 Streak bonuses · achievements/badges · XP & levels
- ⬜ Trivia battles (1v1) · clan/guild competitions
- ⬜ Boss-quiz events · seasonal themed events

## 5. Money — Admin Revenue Engine 💰
- ✅ Revenue dashboard (UPI ₹, crypto $, BGM sold, top buyers, today)
- ✅ UPI auto-verify (FamPay email) · ✅ crypto (Heleket)
- ✅ Live-editable pricing (⚙️ Pricing) — download/request cost, claim range,
  BGM price; applies instantly, no redeploy
- ✅ BGM bundles (buy more, save — bonus % on UPI & crypto purchases)
- ✅ Gift BGM to a friend (atomic transfer)
- 🔜 BGM price wired into Buy menus · per-item dynamic surge pricing
- 🔜 Daily deals / flash sales / happy-hour multipliers
- 🔜 Coupon & promo campaigns with usage caps · first-purchase bonus
- ✅ VIP subscription tiers (Silver/Gold) — BGM-priced, 30d; perks: cheaper/free
  downloads, bigger daily claim, monthly BGM grant; badge in /balance
- ✅ Flash sales / happy-hour (admin-fired timed bonus on all purchases;
  banner on dashboard + Buy menu; locked in at purchase time)
- ✅ Sponsored/featured book slots — admin features a title for N days; ⭐ Featured
  section in Discover (paid placement)
- 🔜 Coupons (purchase discounts)
- 🔜 Sell ad slots inside the bot · affiliate payouts
- 🔜 Abandoned-cart / win-back nudges · low-balance upsell
- ⬜ Gift cards · tipping authors/uploaders
- ⬜ Revenue forecasting · cohort LTV · churn alerts
- ⬜ Per-admin commission tracking · payout ledger
- ⬜ Tax/INR↔USD reporting · CSV/Sheet export

## 6. Admin Power Tools (ease of working)
- ✅ Admin panel, ban/unban, request queue, broadcast, question bank
- ✅ Live settings/pricing editor · revenue dashboard · flash-sale · featured
- ✅ Add BGM to a user · 👤 360° user lookup (balance/VIP/requests/flags)
- ✅ Maintenance mode (blocks non-admins; middleware-enforced)
- ✅ Admin Mini-App dashboard (📊 users/VIP/archive/downloads, revenue ₹+$,
  requests, BGM/BCN circulation, maintenance state; admin-gated initData)
- ✅ Bulk BGM grant (to all users, atomic update_many)
- 🔜 Bulk message / bulk ban · dashboard write-ops
- 🔜 Scheduled broadcasts · A/B broadcast · audience segments · feature flags
- 🔜 Role-based admin permissions (granular) · audit log
- 🔜 Auto-moderation rules · spam/abuse detection
- ⬜ Canned replies · macro buttons · staff shifts
- ⬜ Impersonate-view (see the bot as a user) · sandbox test mode
- ⬜ Health/metrics dashboard · error feed · uptime alerts

## 7. Growth & Virality
- ✅ Referrals (+0.5/+0.25 BGM) + leaderboard
- ✅ Referral milestone bonuses (5/10/25/50/100 → escalating BGM)
- 🔜 Referral contests
- 🔜 Share-to-earn · invite quests · streak rewards
- ✅ Daily Spin-the-Wheel (free, weighted BGM prizes, once/day, atomic)
- ✅ Daily login-streak reward (escalating to day-7, resets on a missed day)
- 🔜 Loot crates · quests/missions board
- 🔜 Quests/missions with BGM payouts · battle pass
- ⬜ Social proof ("X downloaded today") · viral share cards
- ⬜ Channel cross-promo swaps · partner programs

## 8. Engagement & Retention
- ✅ Daily BCN claim · ratings · support inbox
- 🔜 Push reminders (claim ready, watchlist hit, unfinished book)
- 🔜 Personalized weekly digest · "for you" feed
- 🔜 Notification center & preferences
- ⬜ Streak insurance · comeback bonuses · anniversary gifts

## 9. Personalization & Profiles
- 🔜 Public profile (badges, books read, level) · vanity handle
- 🔜 Avatar/frame cosmetics (buy with BGM) · themes
- 🔜 Reading DNA (genre breakdown) · stats page
- ⬜ Shareable profile cards · friends/following

## 10. Social & Community
- 🔜 Book clubs / reading rooms · group reading challenges
- 🔜 Comments/reactions on titles · discussion threads
- 🔜 Leaderboards (downloads, quizzes, referrals, reading time)
- ⬜ Buddy reading · shared shelves · author AMAs

## 11. Content Pipeline (uploaders/admins)
- ✅ Telethon backfill (30k archive) · real-time indexer · watchlist
- 🔜 Auto-tagging & metadata enrichment (AI) · cover fetch
- 🔜 Duplicate detection · quality flags · OCR for scans
- 🔜 Uploader rewards/leaderboard · bounty requests
- ⬜ Auto-categorize into genres · series grouping · multi-format linking

## 12. Trust, Safety & Anti-Abuse
- ✅ Safe captcha · atomic token/payment paths · ban system
- 🔜 Rate limiting · anti-multiaccount · device fingerprint (privacy-safe)
- 🔜 Fraud/chargeback flags · refund controls · velocity checks
- ⬜ Content reporting · DMCA workflow · age-gating

## 13. Platform & Reliability
- ✅ Mongo multi-cluster failover · health endpoint · Dockerized
- 🔜 Redis cache layer · rate-limit store · job queue
- 🔜 Structured logging & metrics · Sentry-style error capture
- 🔜 Backups · data export (GDPR) · per-user data delete
- ⬜ Horizontal scaling · webhook mode · CDN for media

## 14. Localization & Accessibility
- 🔜 Multi-language UI (i18n) · per-user language
- 🔜 Currency localization · RTL support
- ⬜ Dyslexia font · high-contrast · screen-reader friendly captions

---

### Build cadence
Each turn ships one tested, committed batch (3–6 real features). This file is
the source of truth — items flip ✅ as they land. Current focus: Pillars 1
(reader), 2 (summaries), 5 (revenue).
