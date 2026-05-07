# Week 7 — Telegram alpha-channels collector

## What this week adds

A Telethon-based listener inside the existing ingest service that connects
to your Telegram account in **read-only** mode and streams new messages
from the public channels you specify. Free, real-time, and often the
highest-signal source for early narratives.

Architecture: same `ingest` container, runs as a long-lived task alongside
the polling collectors. New messages flow into the same `posts` table with
`source = 'telegram'`.

## One-time setup

### 1. Get Telegram API credentials

1. Visit https://my.telegram.org and log in with your phone number.
2. Go to **API development tools**.
3. Create an app (any name, "Other" platform). Save the `api_id` (an int)
   and `api_hash` (a long string).

### 2. Add to .env

```
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_CHANNELS=cryptosignalsorg,whalealert_io,bitcoin
```

`TELEGRAM_CHANNELS` is a comma-separated list of public channel
usernames (no `@`, no `https://t.me/`).

### 3. Authenticate once

The first connection requires an interactive login. Run:

```powershell
docker compose run --rm -it ingest python auth_telegram.py
```

It will prompt for:
- Phone (international format, e.g. `+49...`)
- The 5-digit code Telegram sends you (in the Telegram app)
- 2FA password if you have one enabled

A session file is saved to a Docker volume (`telegram_session`) and reused
on every restart afterward. **Do not delete that volume** — losing it
means re-doing the interactive login.

### 4. Restart the ingest service

```powershell
docker compose up -d --force-recreate ingest
docker compose logs -f ingest
```

You should see:
```json
{"event":"telegram_starting","channels":["cryptosignalsorg",...]}
{"event":"telegram_connected"}
```

And every time a tracked channel posts about one of your tracked coins:
```json
{"event":"telegram_message_stored","chat":"cryptosignalsorg","coin":"BTC","msg_id":12345}
```

## Verify

```sql
-- Recent Telegram posts
SELECT author, coin, posted_at, left(text, 80) FROM posts
WHERE source = 'telegram'
ORDER BY posted_at DESC LIMIT 10;
```

## Picking channels

Avoid:
- Paid signal channels — selection bias, often pump-and-dump.
- Channels promising guaranteed returns.
- Anything you can't read directly (private/invite-only).

Good starters (verify they're still active):
- `whalealert_io` — large on-chain transfers
- `cointelegraph` — news headlines
- Project-specific official channels (e.g. `solana`, `ethereum`)

## What's NOT happening

- Private/invite-only channels — Telethon can read them too if your
  account is a member, but legally and ethically dicier. Stick to public.
- Replies / reactions — we only ingest top-level messages. Adding reaction
  counts is a small Telethon API extension; defer until you see the
  data pipeline holding up.
- Ticker disambiguation — same naive `detect_coin` logic as Reddit.
  Channel-specific parsing (e.g. structured trade calls) is post-MVP.

## Common issues

- **`AuthKeyUnregisteredError`** — your session file was deleted or
  invalidated server-side. Re-run `auth_telegram.py`.
- **`FloodWaitError`** — you connected too many times. Wait the requested
  seconds and try again. Don't loop reconnects.
- **No messages arriving** — check the channel username is exact (no `@`
  prefix, case-insensitive). Verify you can open it from the Telegram app.
