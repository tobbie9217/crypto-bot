"""
One-time interactive Telegram auth.

Run this once after configuring TELEGRAM_API_ID/HASH in .env to create a
persistent session file. Subsequent ingest container restarts will reuse
the session and won't prompt.

  docker compose run --rm -it ingest python auth_telegram.py

You'll be asked for:
  1. Your phone number (international format, e.g. +49...)
  2. The login code Telegram sends to you
  3. Your 2FA password if 2FA is enabled
"""
import asyncio
import os
import sys

from telethon import TelegramClient


async def main() -> None:
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session_path = os.environ.get("TELEGRAM_SESSION", "/app/sessions/telegram.session")

    if not api_id or not api_hash:
        print("ERROR: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(session_path), exist_ok=True)

    client = TelegramClient(session_path, int(api_id), api_hash)
    await client.start()  # interactive prompt
    me = await client.get_me()
    print(f"Authenticated as @{me.username or me.first_name} (id={me.id})")
    print(f"Session saved to {session_path}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
