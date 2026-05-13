"""
Binance Futures OHLCV candles — incremental live collector.

Pulls the most-recent N candles per active USDT-M perpetual for every
configured timeframe (1m, 5m, 1h) into the `ohlcv` table on each cycle.
Re-inserting the still-forming candle is idempotent via ON CONFLICT
DO UPDATE — the row updates in place as price moves through the candle.

This is the foundation for any per-pair historical analysis we want to
do in SQL (regime detection, correlation, prior-day-high features, etc.)
without going back to Binance every time. The deep_research.pdf audit
flagged "Real OHLCV stored" as the highest-value missing free dataset.

Backfill of historical candles is a separate one-shot script
(`scripts/backfill_ohlcv.py`).
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

EXCHANGE_NAME = "binance_futures"
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

# Timeframes we collect each cycle. Order matters only for log readability.
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "1h")

# How many trailing candles to fetch per (symbol, timeframe). 3 covers
# the in-progress candle plus 1-2 prior closed candles, so we never miss
# a candle even with scheduler drift.
LIVE_CANDLE_LIMIT = 3

# Sleep between per-symbol/timeframe calls. ~50ms × 3 timeframes × ~330
# perps ≈ 50s — well within Binance's 2400/min unauth weight budget
# (klines limit=3 is weight=1).
PER_CALL_SLEEP_S = 0.05


def _perp_to_base(perp_symbol: str) -> Optional[str]:
    """BTCUSDT -> 'BTC', 1000SHIBUSDT -> 'SHIB'. None if not a USDT perp."""
    if not perp_symbol.endswith("USDT"):
        return None
    base = perp_symbol[:-4]
    if base.startswith("1000"):
        base = base[4:]
    return base or None


def _parse_kline(
    raw: list,
    exchange: str,
    symbol: str,
    coin: str,
    timeframe: str,
) -> Optional[tuple]:
    """Convert a Binance kline array to the tuple shape DB.insert_ohlcv_rows expects."""
    try:
        ts_ms = int(raw[0])
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        o = float(raw[1])
        h = float(raw[2])
        l = float(raw[3])
        c = float(raw[4])
        v = float(raw[5])
        qv = float(raw[7]) if raw[7] is not None else None
        n = int(raw[8]) if raw[8] is not None else None
        tb_base = float(raw[9]) if raw[9] is not None else None
        tb_quote = float(raw[10]) if raw[10] is not None else None
    except (TypeError, ValueError, IndexError):
        return None
    return (
        exchange, symbol, coin, timeframe, ts,
        o, h, l, c, v,
        qv, n, tb_base, tb_quote,
    )


async def collect_binance_ohlcv(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+binance ohlcv)"},
    ) as client:
        try:
            resp = await client.get(EXCHANGE_INFO_URL)
            resp.raise_for_status()
            live_perps: dict[str, str] = {}
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
                    live_perps[sym] = base
        except Exception as e:  # noqa: BLE001
            log.warning("binance_ohlcv_exchange_info_failed", error=str(e))
            return

        all_rows: list[tuple] = []
        fetch_failures = 0

        for sym, base in live_perps.items():
            for timeframe in TIMEFRAMES:
                try:
                    r = await client.get(
                        KLINES_URL,
                        params={"symbol": sym, "interval": timeframe, "limit": LIVE_CANDLE_LIMIT},
                    )
                    r.raise_for_status()
                    klines = r.json()
                except Exception as e:  # noqa: BLE001
                    fetch_failures += 1
                    log.warning(
                        "binance_ohlcv_fetch_failed",
                        symbol=sym, timeframe=timeframe, error=str(e),
                    )
                    await asyncio.sleep(PER_CALL_SLEEP_S)
                    continue
                for kline in klines:
                    row = _parse_kline(kline, EXCHANGE_NAME, sym, base, timeframe)
                    if row is not None:
                        all_rows.append(row)
                await asyncio.sleep(PER_CALL_SLEEP_S)

        inserted = await db.insert_ohlcv_rows(all_rows)

    log.info(
        "binance_ohlcv_collected",
        perps=len(live_perps),
        timeframes=len(TIMEFRAMES),
        candles_upserted=inserted,
        fetch_failures=fetch_failures,
    )
