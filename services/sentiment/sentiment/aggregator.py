import asyncpg

# Per-coin, per-hour mean of CryptoBERT scores over the last 7 days.
# Idempotent — re-running just refreshes the same buckets.
AGGREGATE_SQL = """
INSERT INTO sentiment_aggregates (coin, time_window, bucket_start, mean_score, post_count)
SELECT
    p.coin,
    '1h' AS time_window,
    date_trunc('hour', p.posted_at) AS bucket_start,
    AVG(s.score) AS mean_score,
    COUNT(*) AS post_count
FROM posts p
JOIN sentiment_scores s ON s.post_id = p.id
WHERE s.model = 'cryptobert'
  AND p.coin IS NOT NULL
  AND p.posted_at >= NOW() - INTERVAL '7 days'
GROUP BY p.coin, date_trunc('hour', p.posted_at)
ON CONFLICT (coin, time_window, bucket_start) DO UPDATE SET
    mean_score = EXCLUDED.mean_score,
    post_count = EXCLUDED.post_count,
    updated_at = NOW();
"""

# Z-score each bucket against the coin's last-7-days mean/std.
# A z above ~1.5 signals an unusual sentiment swing for that coin.
ZSCORE_SQL = """
WITH stats AS (
    SELECT coin,
           AVG(mean_score) AS mu,
           NULLIF(STDDEV(mean_score), 0) AS sigma
    FROM sentiment_aggregates
    WHERE time_window = '1h' AND bucket_start >= NOW() - INTERVAL '7 days'
    GROUP BY coin
)
UPDATE sentiment_aggregates a
SET z_score = (a.mean_score - s.mu) / s.sigma,
    updated_at = NOW()
FROM stats s
WHERE a.coin = s.coin
  AND a.time_window = '1h'
  AND a.bucket_start >= NOW() - INTERVAL '7 days'
  AND s.sigma IS NOT NULL;
"""


async def compute_aggregates(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(AGGREGATE_SQL)
        await conn.execute(ZSCORE_SQL)


# On-chain rollups: per (coin, metric), bucket to the hour, take the latest
# value in the bucket, and compute % delta vs prior bucket and z-score vs
# the 7-day baseline.
ONCHAIN_AGGREGATE_SQL = """
INSERT INTO onchain_aggregates (coin, metric, time_window, bucket_start, value, sample_count)
SELECT
    coin,
    metric,
    '1h' AS time_window,
    date_trunc('hour', observed_at) AS bucket_start,
    (ARRAY_AGG(value ORDER BY observed_at DESC))[1] AS value,
    COUNT(*) AS sample_count
FROM onchain_metrics
WHERE observed_at >= NOW() - INTERVAL '7 days'
GROUP BY coin, metric, date_trunc('hour', observed_at)
ON CONFLICT (coin, metric, time_window, bucket_start) DO UPDATE SET
    value = EXCLUDED.value,
    sample_count = EXCLUDED.sample_count,
    updated_at = NOW();
"""

ONCHAIN_DELTA_SQL = """
WITH ordered AS (
    SELECT coin, metric, time_window, bucket_start, value,
           LAG(value) OVER (PARTITION BY coin, metric ORDER BY bucket_start) AS prev_value
    FROM onchain_aggregates
    WHERE bucket_start >= NOW() - INTERVAL '7 days'
)
UPDATE onchain_aggregates a
SET delta_pct = CASE
        WHEN o.prev_value IS NULL OR o.prev_value = 0 THEN NULL
        ELSE (o.value - o.prev_value) / o.prev_value * 100.0
    END,
    updated_at = NOW()
FROM ordered o
WHERE a.coin = o.coin
  AND a.metric = o.metric
  AND a.time_window = o.time_window
  AND a.bucket_start = o.bucket_start;
"""

ONCHAIN_ZSCORE_SQL = """
WITH stats AS (
    SELECT coin, metric,
           AVG(value) AS mu,
           NULLIF(STDDEV(value), 0) AS sigma
    FROM onchain_aggregates
    WHERE time_window = '1h' AND bucket_start >= NOW() - INTERVAL '7 days'
    GROUP BY coin, metric
)
UPDATE onchain_aggregates a
SET z_score = (a.value - s.mu) / s.sigma,
    updated_at = NOW()
FROM stats s
WHERE a.coin = s.coin
  AND a.metric = s.metric
  AND a.time_window = '1h'
  AND a.bucket_start >= NOW() - INTERVAL '7 days'
  AND s.sigma IS NOT NULL;
"""


async def compute_onchain_aggregates(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(ONCHAIN_AGGREGATE_SQL)
        await conn.execute(ONCHAIN_DELTA_SQL)
        await conn.execute(ONCHAIN_ZSCORE_SQL)
