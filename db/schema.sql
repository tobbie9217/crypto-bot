-- Schema for crypto-bot. Loaded by Postgres on first container start
-- via docker-entrypoint-initdb.d. To re-apply, drop the postgres_data volume.

CREATE TABLE IF NOT EXISTS posts (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,        -- 'reddit', 'cryptopanic', 'lunarcrush', 'telegram'
    source_id   TEXT NOT NULL,        -- unique within source
    coin        TEXT,                 -- 'BTC', 'ETH', etc.
    text        TEXT NOT NULL,
    author      TEXT,
    url         TEXT,
    posted_at   TIMESTAMPTZ NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw         JSONB,
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_coin_posted   ON posts (coin, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_source_fetch  ON posts (source, fetched_at DESC);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id          BIGSERIAL PRIMARY KEY,
    post_id     BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,        -- 'cryptobert', 'finbert', 'vader'
    score       DOUBLE PRECISION NOT NULL,   -- normalized -1..1 (bullish positive)
    label       TEXT,                 -- 'Bullish' / 'Neutral' / 'Bearish'
    confidence  DOUBLE PRECISION,     -- 0..1, model's softmax for picked label
    scored_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (post_id, model)
);

CREATE INDEX IF NOT EXISTS idx_scores_post     ON sentiment_scores (post_id);
CREATE INDEX IF NOT EXISTS idx_scores_scored   ON sentiment_scores (scored_at DESC);

-- Per-coin rolling aggregates the strategy reads on each candle
CREATE TABLE IF NOT EXISTS sentiment_aggregates (
    coin          TEXT NOT NULL,
    time_window   TEXT NOT NULL,      -- '1h', '24h'
    bucket_start  TIMESTAMPTZ NOT NULL,
    mean_score    DOUBLE PRECISION NOT NULL,
    post_count    INT NOT NULL,
    z_score       DOUBLE PRECISION,    -- vs 7-day baseline
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (coin, time_window, bucket_start)
);

CREATE INDEX IF NOT EXISTS idx_agg_recent ON sentiment_aggregates (coin, time_window, bucket_start DESC);

-- Audit/event log: kill-switch flags, manual pauses, errors, etc.
CREATE TABLE IF NOT EXISTS bot_events (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_type_created ON bot_events (event_type, created_at DESC);
