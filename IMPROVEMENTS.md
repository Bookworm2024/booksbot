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
- 🔜 "Similar to this" recommendations · mood-based picks
- 🔜 Author spotlight · series detector & next-in-series
- 🔜 Genre browse hub · curated collections / shelves · reading challenges
- ⬜ Goodreads-style ratings/reviews per title
- ⬜ Personal reading goals & yearly wrap-up
- ⬜ Wishlist/TBR list with reminders

## 3. Powerful Search
- 🔜 Author filter (needs author metadata) · saved searches · search history
- ⬜ Semantic (embedding) search

## 4. Games & Brain
- 🔜 Cover-Guess · Title Anagram/Word-Builder · Speed-Reading WPM · Memory match
- 🔜 Literary Hangman · Crossword · daily challenge · weekly tournament
- 🔜 Game streak bonuses · achievements/badges · global XP & levels
- ⬜ Trivia battles (1v1) · clan/guild competitions
- ⬜ Boss-quiz events · seasonal themed events

## 5. Money — Admin Revenue Engine
- 🔜 BGM price wired into Buy menus · per-item dynamic surge pricing
- 🔜 Coupon & promo campaigns with usage caps · first-purchase bonus
- 🔜 Sell ad slots inside the bot · affiliate payouts
- 🔜 Abandoned-cart / win-back nudges · low-balance upsell
- ⬜ Gift cards · tipping authors/uploaders
- ⬜ Revenue forecasting · cohort LTV · churn alerts
- ⬜ Per-admin commission tracking · payout ledger
- ⬜ Tax / INR↔USD reporting · CSV/Sheet export

## 6. Admin Power Tools
- 🔜 Bulk ban · A/B broadcast · feature flags
- 🔜 Role-based admin permissions (granular) · audit log
- 🔜 Auto-moderation rules · spam/abuse detection
- ⬜ Canned replies · macro buttons · staff shifts
- ⬜ Impersonate-view (see the bot as a user) · sandbox test mode
- ⬜ Health/metrics dashboard · error feed · uptime alerts

## 7. Growth & Virality
- 🔜 Referral contests
- 🔜 Share-to-earn · invite quests
- 🔜 Loot crates · battle pass
- ⬜ Social proof ("X downloaded today") · viral share cards
- ⬜ Channel cross-promo swaps · partner programs

## 8. Engagement & Retention
- 🔜 Personalized weekly digest · "for you" feed
- ⬜ Streak insurance · comeback bonuses · anniversary gifts

## 9. Personalization & Profiles
- 🔜 Avatar/frame cosmetics (buy with BGM) · vanity handle
- 🔜 Reading DNA (genre breakdown)
- ⬜ Shareable profile cards · friends/following

## 10. Social & Community
- 🔜 Book clubs / reading rooms · group reading challenges
- 🔜 Comments/reactions on titles · discussion threads
- ⬜ Buddy reading · shared shelves · author AMAs

## 11. Content Pipeline
- 🔜 Metadata enrichment · cover fetch · duplicate detection · OCR for scans
- 🔜 Uploader rewards/leaderboard · bounty requests
- ⬜ Auto-categorize into genres · series grouping · multi-format linking

## 12. Trust, Safety & Anti-Abuse
- 🔜 Anti-multiaccount · device fingerprint (privacy-safe)
- 🔜 Fraud/chargeback flags · refund controls · velocity checks
- ⬜ Content reporting · DMCA workflow · age-gating

## 13. Platform & Reliability
- 🔜 Redis cache layer · rate-limit store · job queue
- 🔜 Structured logging & metrics · Sentry-style error capture
- 🔜 Backups · data export (GDPR) · per-user data delete
- ⬜ Horizontal scaling · webhook mode · CDN for media

## 14. Localization & Accessibility
- 🔜 Multi-language UI (i18n) · per-user language
- 🔜 Currency localization · RTL support
- ⬜ Dyslexia font · high-contrast · screen-reader friendly captions

---

### Operational (not a feature, but pending)
- Deploy the latest `main` to Koyeb (each batch needs a redeploy to go live).
- Run the Telethon backfill once `API_ID`/`API_HASH`/`TELETHON_SESSION` +
  `FILE_CHANNEL_ID` are set (indexes the ~30k archive; now also stamps trigrams).
