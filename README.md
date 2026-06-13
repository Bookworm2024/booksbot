# 📚 BooksBot (`@getfreebooksbot`)

A Telegram books/audiobooks bot — rebuilt from the TeleBotCreator no-code
engine into a real, self-hosted **aiogram + MongoDB** application that runs on
Koyeb (or any Docker host), with **Mini Apps** (reader, player, games), a
searchable index of a large file archive, and a two-tier admin centre.

> **Status: Phase 1 (foundation).** Runnable skeleton — onboarding, join-gate,
> coloured dashboard, Mongo layer, admin entry + ban/unban, Mini-App host, and
> the Telethon backfill scaffold. Feature flows are tracked in [PLAN.md](PLAN.md).

## Stack
- **aiogram 3.25+** — bot framework (coloured buttons via `style=`)
- **MongoDB** (motor) — data, with multi-cluster failover
- **aiohttp** — `/health` + Mini-App static hosting (`/app/*`)
- **Telethon** — one-time userbot backfill of the file channel history

## Run locally
```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env          # fill in BOT_TOKEN + MONGO_URL at minimum
python bot.py
```

## Index the 30k-file archive
```bash
python tools/generate_session.py   # mint TELETHON_SESSION (one time)
# put API_ID / API_HASH / TELETHON_SESSION / FILE_CHANNEL_ID in .env
python tools/backfill.py           # walks channel history → Mongo `files`
```

## Deploy to Koyeb
1. Push this repo to GitHub (`Bookworm2024/booksbot`).
2. Koyeb → Create Service → GitHub → this repo → **Dockerfile** builder.
3. Set env vars (see `.env.example`). Set `BOT_PUBLIC_URL` to the Koyeb URL.
4. Health check path: `/health`.

## Layout
```
bot.py                 entry: polling + web server
config.py              env-driven config
database/connection.py Mongo manager (failover, kv store, indexes)
handlers/              start.py (onboarding+dashboard), admin.py
middlewares/ban.py     block banned users
utils/                 keyboards.py (coloured), users.py
tools/                 generate_session.py, backfill.py (Telethon)
web_app/               Mini-App static host
```
