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

-- On-chain / market metrics. Time series of structured numeric values
-- (TVL, price, 24h volume, etc.) keyed by coin + metric + source.
CREATE TABLE IF NOT EXISTS onchain_metrics (
    id           BIGSERIAL PRIMARY KEY,
    coin         TEXT NOT NULL,
    metric       TEXT NOT NULL,        -- 'price_usd', 'volume_24h', 'market_cap', 'price_change_24h', 'chain_tvl'
    value        DOUBLE PRECISION NOT NULL,
    source       TEXT NOT NULL,        -- 'coingecko', 'defillama'
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw          JSONB,
    UNIQUE (coin, metric, source, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_onchain_coin_metric_time
    ON onchain_metrics (coin, metric, observed_at DESC);

-- Hourly rollups the strategy reads. Mirrors sentiment_aggregates shape.
CREATE TABLE IF NOT EXISTS onchain_aggregates (
    coin          TEXT NOT NULL,
    metric        TEXT NOT NULL,
    time_window   TEXT NOT NULL,      -- '1h'
    bucket_start  TIMESTAMPTZ NOT NULL,
    value         DOUBLE PRECISION NOT NULL,   -- last value in bucket
    delta_pct     DOUBLE PRECISION,            -- vs prior bucket
    z_score       DOUBLE PRECISION,            -- vs 7-day baseline
    sample_count  INT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (coin, metric, time_window, bucket_start)
);

CREATE INDEX IF NOT EXISTS idx_onchain_agg_recent
    ON onchain_aggregates (coin, metric, time_window, bucket_start DESC);

-- Per-trade audit. Strategy hooks (confirm_trade_entry / confirm_trade_exit)
-- write a row at every entry and exit with the full feature snapshot the
-- strategy saw — so we can attribute outcomes to signals after the fact.
CREATE TABLE IF NOT EXISTS trade_journal (
    id                BIGSERIAL PRIMARY KEY,
    trade_id          BIGINT,
    pair              TEXT NOT NULL,
    coin              TEXT NOT NULL,
    side              TEXT NOT NULL,        -- 'long'
    event             TEXT NOT NULL,        -- 'entry' | 'exit'
    enter_tag         TEXT,                 -- 'strict' | 'momentum'
    exit_reason       TEXT,
    rate              DOUBLE PRECISION,
    profit_ratio      DOUBLE PRECISION,
    profit_abs        DOUBLE PRECISION,
    duration_seconds  INTEGER,              -- only on exit rows
    max_profit_ratio  DOUBLE PRECISION,     -- intra-trade peak
    min_profit_ratio  DOUBLE PRECISION,     -- intra-trade trough
    thresholds        JSONB,                -- snapshot of strategy thresholds at entry
    features          JSONB NOT NULL,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journal_pair_time ON trade_journal (pair, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_event     ON trade_journal (event, occurred_at DESC);

-- Historical OHLCV candles stored per (exchange, symbol, timeframe).
-- Filled by binance_ohlcv collector (current) + backfill_ohlcv script
-- (history). Primary key is composite so re-inserting an in-progress
-- candle each cycle is idempotent — the candle row updates in place
-- until it closes.
--
-- `taker_buy_base_volume` / `taker_buy_quote_volume` come straight from
-- Binance klines and are the buy-side share of the candle's total
-- volume — useful as a per-candle order-flow feature.
CREATE TABLE IF NOT EXISTS ohlcv (
    exchange               TEXT NOT NULL,        -- 'binance_futures'
    symbol                 TEXT NOT NULL,        -- 'BTCUSDT' (exchange symbol)
    coin                   TEXT NOT NULL,        -- 'BTC' (base, normalized)
    timeframe              TEXT NOT NULL,        -- '1m', '5m', '1h'
    ts                     TIMESTAMPTZ NOT NULL, -- candle open time
    open                   DOUBLE PRECISION NOT NULL,
    high                   DOUBLE PRECISION NOT NULL,
    low                    DOUBLE PRECISION NOT NULL,
    close                  DOUBLE PRECISION NOT NULL,
    volume                 DOUBLE PRECISION NOT NULL,
    quote_volume           DOUBLE PRECISION,
    trades                 INTEGER,
    taker_buy_base_volume  DOUBLE PRECISION,
    taker_buy_quote_volume DOUBLE PRECISION,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (exchange, symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_coin_tf_ts
    ON ohlcv (coin, timeframe, ts DESC);

-- Audit/event log: kill-switch flags, manual pauses, errors, etc.
CREATE TABLE IF NOT EXISTS bot_events (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_type_created ON bot_events (event_type, created_at DESC);
