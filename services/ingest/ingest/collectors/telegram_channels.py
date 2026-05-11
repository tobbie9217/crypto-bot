"""
Telegram public-channels listener.

Uses Telethon (MTProto) to read messages from public crypto channels in
real-time and writes them to the same `posts` table the other collectors
use. Free.

This collector is **long-running** (event-driven), unlike the polling
collectors. It runs as its own task in `main.py`.

First-run authentication is interactive — see docs/week7-telegram-channels.md.
The session file is persisted to a Docker volume so subsequent restarts
don't require re-auth.
"""
from datetime import datetime, timezone

import structlog
from telethon import TelegramClient, events

from ..db import DB
from ..settings import settings
from .reddit import detect_coin

log = structlog.get_logger()


async def run_telegram_listener(db: DB) -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        log.warning("telegram_disabled", reason="missing_credentials")
        return
    if not settings.telegram_channels_list:
        log.warning("telegram_disabled", reason="no_channels_configured")
        return

    client = TelegramClient(
        settings.telegram_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    @client.on(events.NewMessage(chats=settings.telegram_channels_list))
    async def on_message(event):
        msg = event.message
        text = (msg.message or "").strip()
        chat = await event.get_chat()
        chat_username = getattr(chat, "username", None) or str(chat.id)
        if not text:
            log.info("telegram_message_skipped", chat=chat_username, reason="empty")
            return
        coin = detect_coin(text)
        if coin is None:
            log.info(
                "telegram_message_skipped",
                chat=chat_username,
                reason="no_tracked_coin",
                preview=text[:120],
            )
            return

        pid = await db.insert_post(
            source="telegram",
            source_id=f"{chat.id}:{msg.id}",
            coin=coin,
            text=text[:8000],
            author=chat_username,
            url=(
                f"https://t.me/{chat_username}/{msg.id}"
                if getattr(chat, "username", None)
                else None
            ),
            posted_at=msg.date or datetime.now(timezone.utc),
            raw={
                "chat_id": chat.id,
                "chat_title": getattr(chat, "title", None),
                "views": getattr(msg, "views", None),
                "forwards": getattr(msg, "forwards", None),
            },
        )
        if pid is not None:
            log.info(
                "telegram_message_stored",
                chat=chat_username,
                coin=coin,
                msg_id=msg.id,
            )

    log.info("telegram_starting", channels=settings.telegram_channels_list)
    await client.start()  # uses cached session; interactive only on first run
    log.info("telegram_connected")
    await client.run_until_disconnected()
