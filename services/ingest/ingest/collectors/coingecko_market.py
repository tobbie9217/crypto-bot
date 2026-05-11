"""
CoinGecko market data collector.

Pulls price, 24h volume, market cap, and 24h % change for every coin in
`coins.TRACKED` in a single `/coins/markets` call. Stores each metric as
a row in `onchain_metrics` so downstream aggregators can compute deltas
and z-scores.

Free public tier: ~10–30 req/min. We poll once every 5 minutes which is
well below the limit.
"""
from datetime import datetime, timezone

import httpx
import structlog

from ..coins import TRACKED
from ..db import DB

log = structlog.get_logger()

URL = "https://api.coingecko.com/api/v3/coins/markets"


async def collect_coingecko_market(db: DB) -> None:
    by_id = {c.coingecko_id: c for c in TRACKED}
    params = {
        "vs_currency": "usd",
        "ids": ",".join(by_id.keys()),
        "order": "market_cap_desc",
        "per_page": str(len(by_id)),
        "page": "1",
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+coingecko market)"},
    ) as client:
        resp = await client.get(URL, params=params)
        resp.raise_for_status()
        rows = resp.json()

    now = datetime.now(timezone.utc)
    inserted = 0
    seen = 0
    for row in rows:
        cg_id = row.get("id")
        spec = by_id.get(cg_id)
        if spec is None:
            continue
        seen += 1
        # Each metric is its own row so the aggregator can treat them uniformly.
        metrics = {
            "price_usd":         row.get("current_price"),
            "volume_24h":        row.get("total_volume"),
            "market_cap":        row.get("market_cap"),
            "price_change_24h":  row.get("price_change_percentage_24h"),
        }
        for metric, value in metrics.items():
            if value is None:
                continue
            pid = await db.insert_onchain_metric(
                coin=spec.symbol,
                metric=metric,
                value=float(value),
                source="coingecko",
                observed_at=now,
                raw={"coingecko_id": cg_id},
            )
            if pid is not None:
                inserted += 1

    log.info("coingecko_market_collected", coins=seen, metrics_inserted=inserted)
