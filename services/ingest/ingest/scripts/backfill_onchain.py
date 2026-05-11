"""
One-shot historical backfill for onchain_metrics.

Pulls history from the same free sources our live collectors use, so the
sentiment service can compute meaningful z-scores immediately instead of
needing a 7-day warmup. Idempotent on the (coin, metric, source,
observed_at) unique constraint — re-running is safe.

Sources:
  * CoinGecko market_chart        → price_usd, volume_24h, market_cap (hourly)
  * DefiLlama historicalChainTvl  → chain_tvl (daily)
  * alternative.me /fng/?limit=N  → fear_greed_index (daily, market-wide)
  * Binance fundingRate           → funding_rate (8h cadence)
  * Binance openInterestHist      → open_interest (1h, last ~20 days)

Run from the host:
  docker compose run --rm ingest python -m ingest.scripts.backfill_onchain
  docker compose run --rm ingest python -m ingest.scripts.backfill_onchain --days 60
"""
import argparse
import asyncio
import logging
from datetime import datetime, timezone

import httpx
import structlog

from ..coins import TRACKED
from ..collectors.defillama import CHAIN_TO_COIN
from ..db import DB
from ..settings import settings


def _configure_logging() -> None:
    logging.basicConfig(level="INFO")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


log = structlog.get_logger()


async def _backfill_coingecko(client: httpx.AsyncClient, db: DB, days: int) -> int:
    """Hourly price / volume / market_cap for each tracked coin."""
    inserted = 0
    for spec in TRACKED:
        try:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{spec.coingecko_id}/market_chart",
                # CoinGecko returns hourly data when 2 <= days <= 90.
                params={"vs_currency": "usd", "days": days},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("backfill_coingecko_failed", coin=spec.symbol, error=str(e))
            await asyncio.sleep(3)
            continue

        for series_name, metric in (
            ("prices", "price_usd"),
            ("total_volumes", "volume_24h"),
            ("market_caps", "market_cap"),
        ):
            for ts_ms, value in data.get(series_name, []):
                if value is None:
                    continue
                observed_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                pid = await db.insert_onchain_metric(
                    coin=spec.symbol,
                    metric=metric,
                    value=float(value),
                    source="coingecko",
                    observed_at=observed_at,
                    raw={"coingecko_id": spec.coingecko_id, "backfill": True},
                )
                if pid is not None:
                    inserted += 1
        # Free-tier rate limit is 10–30 RPM. 32 coins × 3s = 96s; safely under.
        await asyncio.sleep(3)
    return inserted


async def _backfill_defillama(client: httpx.AsyncClient, db: DB) -> int:
    """Full daily TVL history for each tracked chain."""
    inserted = 0
    for chain_name, coin in CHAIN_TO_COIN.items():
        try:
            resp = await client.get(
                f"https://api.llama.fi/v2/historicalChainTvl/{chain_name}"
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("backfill_defillama_failed", chain=chain_name, error=str(e))
            continue
        for entry in data:
            ts = entry.get("date")
            tvl = entry.get("tvl")
            if ts is None or tvl is None:
                continue
            observed_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            pid = await db.insert_onchain_metric(
                coin=coin,
                metric="chain_tvl",
                value=float(tvl),
                source="defillama",
                observed_at=observed_at,
                raw={"chain": chain_name, "backfill": True},
            )
            if pid is not None:
                inserted += 1
        await asyncio.sleep(0.5)
    return inserted


async def _backfill_fear_greed(client: httpx.AsyncClient, db: DB, days: int) -> int:
    try:
        resp = await client.get(f"https://api.alternative.me/fng/?limit={days}")
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("backfill_fng_failed", error=str(e))
        return 0
    inserted = 0
    for entry in payload.get("data", []):
        try:
            value = float(entry["value"])
            ts = int(entry["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        observed_at = datetime.fromtimestamp(ts, tz=timezone.utc)
        pid = await db.insert_onchain_metric(
            coin="MARKET",
            metric="fear_greed_index",
            value=value,
            source="alternative.me",
            observed_at=observed_at,
            raw={"classification": entry.get("value_classification"), "backfill": True},
        )
        if pid is not None:
            inserted += 1
    return inserted


async def _backfill_binance(client: httpx.AsyncClient, db: DB, days: int) -> int:
    """Funding rates (8h cadence) and open interest (1h cadence) per perp."""
    perp_to_coin = {c.binance_perp: c.symbol for c in TRACKED if c.binance_perp}

    # Skip perps Binance doesn't list to avoid 400-spam.
    try:
        resp = await client.get("https://fapi.binance.com/fapi/v1/premiumIndex")
        resp.raise_for_status()
        live_symbols = {row.get("symbol") for row in resp.json() if row.get("symbol")}
    except Exception as e:  # noqa: BLE001
        log.warning("backfill_binance_premium_failed", error=str(e))
        return 0

    inserted = 0
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000

    for sym, coin in perp_to_coin.items():
        if sym not in live_symbols:
            continue

        # Funding rate — up to 1000 records per call (~333 days at 8h cadence).
        try:
            resp = await client.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": sym, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            )
            resp.raise_for_status()
            for row in resp.json():
                ts = row.get("fundingTime")
                rate = row.get("fundingRate")
                if ts is None or rate is None:
                    continue
                observed_at = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                pid = await db.insert_onchain_metric(
                    coin=coin,
                    metric="funding_rate",
                    value=float(rate),
                    source="binance_futures",
                    observed_at=observed_at,
                    raw={"symbol": sym, "backfill": True},
                )
                if pid is not None:
                    inserted += 1
        except Exception as e:  # noqa: BLE001
            log.warning("backfill_binance_funding_failed", symbol=sym, error=str(e))

        # Open interest — 1h period, max 500 records (~20 days). Binance
        # doesn't accept a startTime here without paid plan; we settle for
        # the most-recent 500 buckets which is enough for a z-score baseline.
        try:
            resp = await client.get(
                "https://fapi.binance.com/futures/data/openInterestHist",
                params={"symbol": sym, "period": "1h", "limit": 500},
            )
            resp.raise_for_status()
            for row in resp.json():
                ts = row.get("timestamp")
                oi = row.get("sumOpenInterest")
                if ts is None or oi is None:
                    continue
                observed_at = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                pid = await db.insert_onchain_metric(
                    coin=coin,
                    metric="open_interest",
                    value=float(oi),
                    source="binance_futures",
                    observed_at=observed_at,
                    raw={"symbol": sym, "backfill": True},
                )
                if pid is not None:
                    inserted += 1
        except Exception as e:  # noqa: BLE001
            log.warning("backfill_binance_oi_failed", symbol=sym, error=str(e))

        await asyncio.sleep(0.2)
    return inserted


# Inlined from services/sentiment/sentiment/aggregator.py to avoid a
# cross-service import dependency. Kept in sync manually.
_AGG_SQL = """
INSERT INTO onchain_aggregates (coin, metric, time_window, bucket_start, value, sample_count)
SELECT coin, metric, '1h',
       date_trunc('hour', observed_at),
       (ARRAY_AGG(value ORDER BY observed_at DESC))[1],
       COUNT(*)
FROM onchain_metrics
WHERE observed_at >= NOW() - INTERVAL '90 days'
GROUP BY coin, metric, date_trunc('hour', observed_at)
ON CONFLICT (coin, metric, time_window, bucket_start) DO UPDATE SET
    value = EXCLUDED.value,
    sample_count = EXCLUDED.sample_count,
    updated_at = NOW();
"""

_DELTA_SQL = """
WITH ordered AS (
    SELECT coin, metric, time_window, bucket_start, value,
           LAG(value) OVER (PARTITION BY coin, metric ORDER BY bucket_start) AS prev
    FROM onchain_aggregates
)
UPDATE onchain_aggregates a
SET delta_pct = CASE
        WHEN o.prev IS NULL OR o.prev = 0 THEN NULL
        ELSE (o.value - o.prev) / o.prev * 100.0
    END,
    updated_at = NOW()
FROM ordered o
WHERE a.coin = o.coin AND a.metric = o.metric
  AND a.time_window = o.time_window AND a.bucket_start = o.bucket_start;
"""

_ZSCORE_SQL = """
WITH stats AS (
    SELECT coin, metric, AVG(value) AS mu, NULLIF(STDDEV(value), 0) AS sigma
    FROM onchain_aggregates
    WHERE time_window = '1h' AND bucket_start >= NOW() - INTERVAL '7 days'
    GROUP BY coin, metric
)
UPDATE onchain_aggregates a
SET z_score = (a.value - s.mu) / s.sigma, updated_at = NOW()
FROM stats s
WHERE a.coin = s.coin AND a.metric = s.metric
  AND a.time_window = '1h'
  AND a.bucket_start >= NOW() - INTERVAL '7 days'
  AND s.sigma IS NOT NULL;
"""


async def _refresh_aggregates(db: DB) -> None:
    assert db.pool is not None
    async with db.pool.acquire() as conn:
        await conn.execute(_AGG_SQL)
        await conn.execute(_DELTA_SQL)
        await conn.execute(_ZSCORE_SQL)


async def main(days: int) -> None:
    _configure_logging()
    db = DB(settings.database_url)
    await db.connect()
    try:
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": "crypto-bot/0.1 (+backfill)"}
        ) as client:
            log.info("backfill_start", days=days, coins=len(TRACKED))
            cg = await _backfill_coingecko(client, db, days)
            log.info("backfill_coingecko_done", inserted=cg)
            dl = await _backfill_defillama(client, db)
            log.info("backfill_defillama_done", inserted=dl)
            fg = await _backfill_fear_greed(client, db, days)
            log.info("backfill_fear_greed_done", inserted=fg)
            bn = await _backfill_binance(client, db, days)
            log.info("backfill_binance_done", inserted=bn)
        log.info("aggregating")
        await _refresh_aggregates(db)
        log.info("backfill_complete", total_metrics_inserted=cg + dl + fg + bn)
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="History window in days (CoinGecko hourly: 2–90)")
    args = parser.parse_args()
    asyncio.run(main(args.days))
