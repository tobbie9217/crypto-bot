# Week 2 — Data ingestion

## What this week adds

- A **Postgres** container (`cryptobot-postgres`) with the full schema applied
  on first start (`db/schema.sql`).
- An **ingest** service (`cryptobot-ingest`) that polls three sources on
  schedules and writes to the `posts` table:
  - Reddit (free tier, asyncpraw, 60s interval)
  - CryptoPanic (REST, 120s interval)
  - LunarCrush (REST, 60s interval)

Each collector is independent: missing credentials disables only that one.
The container will keep running with whatever you've configured.

## Set up the API keys

You only need **Reddit** to test ingestion end-to-end. CryptoPanic and
LunarCrush are optional for week 2 — they cost money and Reddit on its
own is enough to verify the pipeline.

### Reddit (free)
1. https://www.reddit.com/prefs/apps -> "create another app"
2. Type: **script**. Redirect URI: `http://localhost`. Name: anything.
3. Copy the **client ID** (under the app name) and the **secret**.
4. Set in `.env`:
   ```
   REDDIT_CLIENT_ID=<id>
   REDDIT_CLIENT_SECRET=<secret>
   ```

### CryptoPanic (optional, free tier exists)
1. https://cryptopanic.com/developers/api/ -> sign up.
2. Set `CRYPTOPANIC_TOKEN=<token>` in `.env`.

### LunarCrush (optional, paid)
Skip until you've decided to pay (~$24+/mo). Set `LUNARCRUSH_TOKEN=` to
enable.

## Run it

```powershell
docker compose up -d postgres ingest
docker compose logs -f ingest
```

You should see one log line per collector per cycle:
```json
{"event":"reddit_collected","seen":75,"inserted":12,"timestamp":"..."}
```

## Verify it's working

Connect to Postgres and look at the data:

```powershell
docker compose exec postgres psql -U cryptobot -d cryptobot
```

```sql
-- Last 10 posts
SELECT source, coin, posted_at, left(text, 60) FROM posts
ORDER BY fetched_at DESC LIMIT 10;

-- Counts per source
SELECT source, count(*) FROM posts GROUP BY source;

-- Per-coin volume
SELECT coin, count(*) FROM posts WHERE coin IS NOT NULL GROUP BY coin ORDER BY 2 DESC;
```

## What's NOT happening yet

- No sentiment scoring — `sentiment_scores` table is empty until week 3.
- No effect on Freqtrade — the strategy still uses pure RSI from week 1.

## Common issues

- **`asyncprawcore.exceptions.ResponseException: 401`** — Reddit credentials
  wrong, or your user-agent is missing. Reddit blocks generic agents.
- **Postgres "schema already exists"** — schema.sql only runs on a *fresh*
  data volume. To re-apply: `docker compose down -v` then `up` again.
  Destroys all data.
- **Ingest container restart loops** — check `docker compose logs ingest`
  for the actual error. Most common: bad `DATABASE_URL` (compose interpolation
  failed because `.env` is missing).
