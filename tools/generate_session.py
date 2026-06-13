"""
tools/generate_session.py — one-time helper to mint a Telethon StringSession.

The Bot API cannot read a channel's *past* messages, so indexing the ~30k
existing files needs a USER session (your own account, which must be a member
of the file channel). Run this once locally:

    python tools/generate_session.py

It prints a TELETHON_SESSION string — put it in .env / the host env panel.
Treat it like a password: it grants full access to that account.

You need API_ID and API_HASH from https://my.telegram.org → API development tools.
"""
import os

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID") or input("API_ID: ").strip())
API_HASH = os.getenv("API_HASH") or input("API_HASH: ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\n=== Your TELETHON_SESSION (keep secret) ===\n")
    print(client.session.save())
    print("\nPaste this into TELETHON_SESSION in your .env / host env.\n")
