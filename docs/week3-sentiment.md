# Week 3 — Sentiment scoring

## What this week adds

A **sentiment** service container that:
1. Polls Postgres for posts not yet scored by CryptoBERT.
2. Runs them through `ElKulako/cryptobert` (CPU, batches of 32).
3. Writes the score to `sentiment_scores` and refreshes
   `sentiment_aggregates` (per-coin, per-hour mean + 7-day z-score).

The CryptoBERT model is **pre-baked into the Docker image** during build,
so the first `docker compose up` after a fresh build does not stall on a
~440MB HuggingFace download.

## Score interpretation

For each post we store:
- `score` in `[-1, 1]` = `P(Bullish) - P(Bearish)` (more useful than raw label)
- `label` ∈ `{Bearish, Neutral, Bullish}`
- `confidence` ∈ `[0, 1]` — softmax probability of the chosen label

Aggregates per coin per hour:
- `mean_score` — straight average across all posts in the bucket
- `post_count` — how many posts contributed (use as a confidence gate)
- `z_score` — vs that coin's own 7-day baseline; >1.5 = unusual positive swing

## Run it

```powershell
docker compose build sentiment      # ~3-5 min, downloads CryptoBERT once
docker compose up -d sentiment
docker compose logs -f sentiment
```

Healthy log lines look like:
```json
{"event":"loading_model","name":"ElKulako/cryptobert"}
{"event":"model_loaded"}
{"event":"connecting_db"}
{"event":"scored","count":32}
```

## Verify

```sql
-- Sample of recent scores
SELECT p.coin, p.source, s.label, s.score::numeric(4,3), left(p.text, 60)
FROM sentiment_scores s
JOIN posts p ON p.id = s.post_id
ORDER BY s.scored_at DESC LIMIT 10;

-- Latest hour aggregates per coin
SELECT coin, bucket_start, mean_score::numeric(4,3),
       z_score::numeric(4,3), post_count
FROM sentiment_aggregates
WHERE window = '1h'
ORDER BY bucket_start DESC LIMIT 20;
```

## Tuning

- `BATCH_SIZE=32` is a safe CPU default. Bumping it speeds throughput but
  also memory usage. On a $5 VPS keep it at 32.
- `IDLE_SLEEP_S=30` controls how often we re-poll when the queue is empty.
  Lower = more responsive, higher = less DB load.

## Common issues

- **OOM during build** — pre-baking weights needs ~2GB RAM. If Docker
  Desktop is throttled, raise the memory limit in Docker settings.
- **`ModuleNotFoundError: torch`** — the requirements layer rebuilt but not
  the torch layer. `docker compose build --no-cache sentiment`.
- **No rows scored** — check `posts` table has rows from week 2 first;
  sentiment service does nothing if nothing's been ingested.
