import asyncpg

# Per-coin, per-hour mean of CryptoBERT scores over the last 7 days.
# Idempotent — re-running just refreshes the same buckets.
AGGREGATE_SQL = """
INSERT INTO sentiment_aggregates (coin, window, bucket_start, mean_score, post_count)
SELECT
    p.coin,
    '1h' AS window,
    date_trunc('hour', p.posted_at) AS bucket_start,
    AVG(s.score) AS mean_score,
    COUNT(*) AS post_count
FROM posts p
JOIN sentiment_scores s ON s.post_id = p.id
WHERE s.model = 'cryptobert'
  AND p.coin IS NOT NULL
  AND p.posted_at >= NOW() - INTERVAL '7 days'
GROUP BY p.coin, date_trunc('hour', p.posted_at)
ON CONFLICT (coin, window, bucket_start) DO UPDATE SET
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
    WHERE window = '1h' AND bucket_start >= NOW() - INTERVAL '7 days'
    GROUP BY coin
)
UPDATE sentiment_aggregates a
SET z_score = (a.mean_score - s.mu) / s.sigma,
    updated_at = NOW()
FROM stats s
WHERE a.coin = s.coin
  AND a.window = '1h'
  AND a.bucket_start >= NOW() - INTERVAL '7 days'
  AND s.sigma IS NOT NULL;
"""


async def compute_aggregates(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(AGGREGATE_SQL)
        await conn.execute(ZSCORE_SQL)
