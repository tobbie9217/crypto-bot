"""
Binance spot ticker — pulls ALL Binance USDT pairs each cycle.

One call to /api/v3/ticker/24hr returns 24h price/volume/change for every
spot symbol on Binance. We store every USDT pair (excluding leveraged
tokens like BTCUP/BTCDOWN/BULL/BEAR which are ETFs, not real pairs).

The `coin` column gets the base asset symbol (e.g. "BTC", "PEPE",
"DOGS"). New listings show up automatically the next cycle they trade.
"""
from datetime import datetime, timezone

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

URL = "https://api.binance.com/api/v3/ticker/24hr"

# Map metric name -> JSON key in the ticker response.
METRIC_KEYS: dict[str, str] = {
    "price_usd":        "lastPrice",
    "volume_24h":       "quoteVolume",
    "price_change_24h": "priceChangePercent",
}

# Leveraged token suffixes — these are ETF-style products, not the underlying.
_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def _is_leveraged_token(base: str) -> bool:
    # Base must be longer than the suffix or it's just a regular short ticker.
    return any(base.endswith(s) and len(base) > len(s) for s in _LEVERAGED_SUFFIXES)


async def collect_binance_spot(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+binance spot)"},
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        tickers = resp.json()

    now = datetime.now(timezone.utc)
    matched = 0
    skipped_leveraged = 0
    inserted = 0
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        if not base or _is_leveraged_token(base):
            skipped_leveraged += 1
            continue
        matched += 1
        for metric, key in METRIC_KEYS.items():
            raw_value = t.get(key)
            if raw_value is None:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            pid = await db.insert_onchain_metric(
                coin=base,
                metric=metric,
                value=value,
                source="binance_spot",
                observed_at=now,
                raw={"symbol": symbol},
            )
            if pid is not None:
                inserted += 1

    log.info(
        "binance_spot_collected",
        matched=matched,
        skipped_leveraged=skipped_leveraged,
        inserted=inserted,
    )
