# 🚀 Deploy BooksBot to Koyeb

Pre-flight verified: 34 routers, health endpoint, 7 Mini Apps. Runs via the
`Dockerfile` (entry `python bot.py`: long-polling + aiohttp web server + 2
background workers — email monitor & reminders).

## 1. Koyeb service
1. app.koyeb.com → **Create Web Service** → **GitHub** → `Bookworm2024/booksbot`, branch `main`.
2. Builder: **Dockerfile** (auto-detected).
3. Instance: **Free** (or Nano), region near you.
4. **Ports / Public networking (CRITICAL):** expose port **8080**, protocol **HTTP**,
   **Public HTTPS access Path = `/`** (root). ⚠️ Do NOT set the public path to
   `/health` — that would route ONLY `/health` and break every Mini App + API.
5. **Health check:** the default TCP check on port 8080 is fine. (If you use an
   HTTP health check, its path is `/health` — but that's the *health-check* path,
   NOT the public-access path in step 4.)
6. Name it (e.g. `booksbot`) → your URL becomes `https://booksbot-<org>.koyeb.app`.

## 2. Environment variables
### Required (bot won't boot without these)
```
BOT_TOKEN=<@hugahugabotbot token from @BotFather>
MONGO_URL=<mongodb+srv://… Atlas string, Network Access 0.0.0.0/0>
SUPER_ADMIN_ID=6011680723
BOT_USERNAME=hugahugabotbot
```
### Strongly recommended
```
FILE_CHANNEL_ID=-100…        # the file channel (bot must be admin there)
LOG_CHANNEL_ID=-100…         # activity logs (optional)
REQUIRED_CHANNELS=@Bookslibraryofficial,@eternalmantra,@thesciencelabs
BOT_PUBLIC_URL=https://booksbot-<org>.koyeb.app   # set AFTER first deploy, then redeploy
```
### Feature keys (each feature activates only when its key is set)
```
# UPI auto-verify (email)
IMAP_HOST=imap.gmail.com
IMAP_USER=rajsom8877@gmail.com
IMAP_PASSWORD=<Gmail App Password>
# Crypto (OxaPay) — webhook auto-set to <BOT_PUBLIC_URL>/oxapay-webhook
OXAPAY_MERCHANT_API_KEY=…
# AI recommendations / summaries / genre tagging
ANTHROPIC_API_KEY=…
# Archive backfill (run tools locally, then optional in env)
API_ID=…
API_HASH=…
TELETHON_SESSION=…
```
Optional tuning: `COLORED_BUTTONS`, `CAPTCHA_ENABLED`, `BGM_PRICE_INR/USD`,
`MIN_BGM_PURCHASE`, `MONGO_DB_NAME`, `TELEGRAM_API_BASE`. Full list in `.env.example`.

## 3. First deploy → wire the URL (the one two-step)
1. Deploy. Watch logs for: `MongoDB ready.` → `Starting polling as @hugahugabotbot`.
2. Copy the Koyeb URL, set `BOT_PUBLIC_URL` to it, **redeploy** (Mini Apps need it).

## 4. Post-deploy
- Add **@hugahugabotbot as admin** to the file channel (needed for delivery + live indexing).
- @BotFather → `/setinline` to enable inline-mode search.
- Index the 30k archive (one time, local):
  `python tools/generate_session.py` → put `TELETHON_SESSION` in env → `python tools/backfill.py`
- Open the bot → `/start` → dashboard. Admin: `/admin` → 📊 Dashboard.

## Notes
- Mongo client is `tz_aware`; all money/credit paths are atomic (audit-hardened).
- Two background loops auto-start; both no-op safely if their keys are unset.
