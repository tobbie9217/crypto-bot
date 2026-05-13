"""
One-shot historical backfill for the `ohlcv` table.

Pulls Binance Futures klines for every active USDT-M perpetual and
inserts them via the same ON CONFLICT DO UPDATE path the live collector
uses — so running this multiple times is safe and idempotent.

Default depths balance "useful history" against "first-run wall-clock":
  * 1h:  90 days  ( ~2 calls per perp at limit=1500)
  * 5m:  30 days  ( ~6 calls per perp)
  * 1m:   7 days  ( ~7 calls per perp)

Override with CLI flags:
  --1h-days N
  --5m-days N
  --1m-days N
  --skip-1m            (handy if you only care about 1h/5m and want speed)

Run from the host:
  docker compose run --rm ingest python -m ingest.scripts.backfill_ohlcv
  docker compose run --rm ingest python -m ingest.scripts.backfill_ohlcv --1m-days 30
"""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import structlog

from ..collectors.binance_ohlcv import (
    EXCHANGE_INFO_URL,
    EXCHANGE_NAME,
    KLINES_URL,
    _parse_kline,
    _perp_to_base,
)
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

# Binance Futures klines: limit=1500 is the max and costs weight=10.
# With sleep ~0.25s between calls we average 240 calls/min = 2400 weight/min,
# right at the unauth limit. Tighten if 429s appear.
KLINE_LIMIT = 1500
PER_CALL_SLEEP_S = 0.25

# Per-timeframe step in milliseconds — used to walk startTime forward.
_TF_MS: dict[str, int] = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}


async def _fetch_active_perps(client: httpx.AsyncClient) -> dict[str, str]:
    resp = await client.get(EXCHANGE_INFO_URL)
    resp.raise_for_status()
    out: dict[str, str] = {}
    for s in resp.json().get("symbols", []):
        sym = s.get("symbol")
        if (
            not sym
            or s.get("status") != "TRADING"
            or s.get("contractType") != "PERPETUAL"
            or s.get("quoteAsset") != "USDT"
        ):
            continue
        base = _perp_to_base(sym)
        if base is not None:
            out[sym] = base
    return out


async def _backfill_one(
    client: httpx.AsyncClient,
    db: DB,
    symbol: str,
    coin: str,
    timeframe: str,
    days: int,
) -> int:
    """Backfill a single (symbol, timeframe). Returns rows upserted."""
    step_ms = _TF_MS[timeframe]
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    cursor = start_ms
    upserted = 0
    while cursor < end_ms:
        try:
            r = await client.get(
                KLINES_URL,
                params={
                    "symbol": symbol,
                    "interval": timeframe,
                    "startTime": cursor,
                    "limit": KLINE_LIMIT,
                },
            )
            r.raise_for_status()
            klines = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning(
                "backfill_ohlcv_fetch_failed",
                symbol=symbol, timeframe=timeframe, cursor=cursor, error=str(e),
            )
            await asyncio.sleep(PER_CALL_SLEEP_S)
            break
        if not klines:
            break
        rows: list[tuple] = []
        for kline in klines:
            row = _parse_kline(kline, EXCHANGE_NAME, symbol, coin, timeframe)
            if row is not None:
                rows.append(row)
        upserted += await db.insert_ohlcv_rows(rows)
        # Walk forward: next cursor = open-time of last candle + step.
        # If the response was short, we're caught up.
        last_open_ms = int(klines[-1][0])
        cursor = last_open_ms + step_ms
        if len(klines) < KLINE_LIMIT:
            break
        await asyncio.sleep(PER_CALL_SLEEP_S)
    return upserted


async def main(
    days_1h: int,
    days_5m: int,
    days_1m: Optional[int],
    only_symbols: Optional[set[str]],
) -> None:
    _configure_logging()
    db = DB(settings.database_url)
    await db.connect()
    try:
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": "crypto-bot/0.1 (+backfill-ohlcv)"}
        ) as client:
            perps = await _fetch_active_perps(client)
            if only_symbols:
                perps = {s: c for s, c in perps.items() if s in only_symbols or c in only_symbols}
            log.info(
                "backfill_ohlcv_start",
                perps=len(perps),
                days_1h=days_1h, days_5m=days_5m, days_1m=days_1m,
            )

            plan: list[tuple[str, int]] = [("1h", days_1h), ("5m", days_5m)]
            if days_1m is not None:
                plan.append(("1m", days_1m))

            total = 0
            for i, (sym, coin) in enumerate(perps.items(), 1):
                for timeframe, days in plan:
                    n = await _backfill_one(client, db, sym, coin, timeframe, days)
                    total += n
                if i % 25 == 0:
                    log.info(
                        "backfill_ohlcv_progress",
                        done=i, of=len(perps), rows_upserted=total,
                    )
        log.info("backfill_ohlcv_complete", rows_upserted=total)
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--1h-days", dest="days_1h", type=int, default=90,
                        help="Days of 1h candles to pull (default: 90)")
    parser.add_argument("--5m-days", dest="days_5m", type=int, default=30,
                        help="Days of 5m candles to pull (default: 30)")
    parser.add_argument("--1m-days", dest="days_1m", type=int, default=7,
                        help="Days of 1m candles to pull (default: 7)")
    parser.add_argument("--skip-1m", action="store_true",
                        help="Skip 1m timeframe entirely")
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated list to limit (BTCUSDT,ETHUSDT or BTC,ETH)")
    args = parser.parse_args()
    only = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    days_1m_arg: Optional[int] = None if args.skip_1m else args.days_1m
    asyncio.run(main(args.days_1h, args.days_5m, days_1m_arg, only))
