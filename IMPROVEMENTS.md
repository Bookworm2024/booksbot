# BooksBot — Pending Improvements (living backlog)

The working list of **unfinished** features. As each one ships it is **deleted
from this file** (and flipped to ✅ in `FEATURES.md`). Keep this file lean — if
it's here, it's not done yet.

`🔜` = next up · `⬜` = later / nice-to-have

> Shipped recently and removed from this list: rotating 5k question bank + pro
> arcade games, free AI provider + /admin AI config, stub-shadow fixes,
> redeem-code & manage-admins panels, live economy levers, flood rate limiting,
> fuzzy/typo-tolerant search + sort, and scheduled + segmented broadcasts.

---

## 1. Universal Reader & Media
- 🔜 MOBI/AZW3 support · CBR comic reader · font-family picker
- 🔜 Per-book notes & highlights · highlight export · "books finished" shelf
- ⬜ Sync reading position across devices · last-page push notification
- ⬜ Dictionary tap-lookup · translate selection · Wikipedia lookup
- ⬜ Adjustable playback EQ for audiobooks · chapter markers

## 2. Book Discovery & Content
- ⬜ TBR reminders (nudge about saved-for-later books)

## 3. Powerful Search
- 🔜 Author filter (needs author metadata)
- ⬜ Semantic (embedding) search

## 4. Games & Brain
- 🔜 Memory match
- 🔜 Crossword
- ⬜ Trivia battles (1v1) · clan/guild competitions
- ⬜ Boss-quiz events · seasonal themed events

## 5. Money — Admin Revenue Engine
- 🔜 Affiliate payouts
- 🔜 Abandoned-cart / win-back nudges · low-balance upsell
- ⬜ Gift cards · tipping authors/uploaders
- ⬜ Revenue forecasting · cohort LTV · churn alerts
- ⬜ Per-admin commission tracking · payout ledger
- ⬜ Tax / INR↔USD reporting · CSV/Sheet export

## 6. Admin Power Tools
- 🔜 A/B broadcast · role-based admin permissions (granular)
- 🔜 Auto-moderation rules · spam/abuse detection
- ⬜ Canned replies · macro buttons · staff shifts
- ⬜ Impersonate-view (see the bot as a user) · sandbox test mode
- ⬜ Health/metrics dashboard · error feed · uptime alerts

## 7. Growth & Virality
- ⬜ Social proof ("X downloaded today") · viral share cards
- ⬜ Channel cross-promo swaps · partner programs

## 8. Engagement & Retention
- ✅ (all current items shipped — see FEATURES.md)

## 9. Personalization & Profiles
- ⬜ Shareable profile cards · friends/following

## 10. Social & Community
- 🔜 Group reading challenges
- ⬜ Buddy reading · shared shelves · author AMAs

## 11. Content Pipeline
- 🔜 Metadata enrichment · cover fetch · duplicate detection · OCR for scans
- 🔜 Uploader rewards/leaderboard · bounty requests
- ⬜ Auto-categorize into genres · series grouping · multi-format linking

## 12. Trust, Safety & Anti-Abuse
- 🔜 Anti-multiaccount · device fingerprint (privacy-safe)
- 🔜 Fraud/chargeback flags · refund controls · velocity checks
- ⬜ DMCA workflow · age-gating

## 13. Platform & Reliability
- 🔜 Redis cache layer · rate-limit store · job queue
- ⬜ Horizontal scaling · webhook mode · CDN for media

## 14. Localization & Accessibility
- 🔜 Multi-language UI (i18n) · per-user language
- 🔜 Currency localization · RTL support
- ⬜ Dyslexia font · high-contrast · screen-reader friendly captions

---

### Tech debt / hardening (surfaced by review workflows)
- ✅ Balance reads now SUM across clusters and spends COMBINE BCN+BGM across all
  clusters (`utils.wallet`: get_balances / spend / charge_bgm / drain_bcn), so a
  cross-cluster duplicate no longer splits a balance, falsely blocks a download,
  or hides a BCN→BGM conversion. cosmetics buy / vanity / gift / VIP all route
  through the shared `charge_bgm`.
- ⬜ Bulk grant (`update_many` per cluster) can still double-credit a user whose
  doc is duplicated across clusters. Low impact (admin-only). Fix later: dedupe to
  a deterministic home cluster `hash(uid) % n`, or consolidate duplicate docs.
- ⬜ BGM is stored as a float → IEEE drift over many transactions. Move the economy
  to integer minor units (the inflowads "wallet integer cents" invariant). Mitigated
  for now: every credit/debit and display passes through `utils.format`
  (`sanitize_amount` clamps to [0, 1e9] & drops NaN/inf; `valid_amount` rejects
  `1e21`/`inf` at input; `fmt_amount` never renders scientific notation).

### Operational (not a feature, but pending)
- Deploy the latest `main` to Koyeb (each batch needs a redeploy to go live).
- The file/database channel is now a LIVE setting: set it in-bot via Admin →
  🗂 File Channel (send the chat id or forward a message). `FILE_CHANNEL_ID` env is
  only a first-run seed/default. Old files: forward them via 📥 Import Old Files,
  or run the Telethon backfill for the bulk ~30k (now reads the live channel too).
