# BooksBot — project context for Claude Code

`@getfreebooksbot`, a Telegram books/audiobooks bot **rebuilt from the
TeleBotCreator (TBC) no-code "BJS" engine** into a real self-hosted app.

## Stack & deploy
- **aiogram 3.25+** (long-polling) · **MongoDB** via motor · **aiohttp** (`/health` + Mini-App host at `/app/*`) · **Telethon** (one-time archive backfill).
- Runs via `Dockerfile` on **Koyeb** (or any Docker host). Entry: `python bot.py`.
- The inflowads project (`Bookworm2024/inflowads`) is the proven stack blueprint — mirror its conventions, but this is a **separate** codebase/repo.

## Workflow
- Repo remote: `github.com/Bookworm2024/booksbot`. Commit to `main`.
- End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Push needs `gh auth login` done on this machine (gh auth is shared across repos).
- **Always read `PLAN.md` first** — it's the source-of-truth roadmap + phase status.

## House rules
- **Coloured keyboards everywhere** — build every button through `utils/keyboards.py` (`style=` field; auto success/primary/danger). Never hand-roll bare buttons.
- **Mini Apps** where they beat chat UI (reader, audiobook player, games).
- **Mongo-backed state** — no in-memory state that's lost on restart; flows idempotent.
- Two admin roles: super admin (`SUPER_ADMIN_ID`) + normal admins (`ADMIN_IDS`).

## Locked decisions
- 30k-file archive search: index the file channel's history with a **Telethon userbot** (`tools/backfill.py`) — the Bot API can't read channel history. Deliver to users via `bot.copy_message(FILE_CHANNEL_ID, msg_id)`.
- Build order: basic runnable bot first; advanced features as later phases (see PLAN.md).

## Layout
`bot.py` entry · `config.py` env config · `database/connection.py` Mongo manager ·
`handlers/` (start, admin) · `middlewares/ban.py` · `utils/` (keyboards, users) ·
`tools/` (generate_session, backfill) · `web_app/` Mini-App static host.
