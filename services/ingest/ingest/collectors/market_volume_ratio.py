"""
Market-wide DEX vs CEX volume ratio — free, no key.

Combines two free public endpoints to compute the relative share of
trading volume happening on-chain (DEXes) vs centralized exchanges:

  * DefiLlama `/overview/dexs`  — total DEX 24h volume (all chains, USD)
  * CoinGecko `/global`         — total crypto 24h volume (all venues)

Three metrics, all written under coin='MARKET':

  * dex_volume_24h_usd     — raw DEX 24h volume
  * total_volume_24h_usd   — raw market 24h volume (DEX + CEX)
  * dex_share_pct          — dex / total × 100 (0..100)

Why care:
  * Rising DEX share often coincides with DeFi-led rallies (Uniswap-era,
    LST/restaking cycles) — useful as a regime context feature.
  * Falling DEX share often reflects institutional/CEX-led trends.
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

DEFILLAMA_DEXS_URL = "https://api.llama.fi/overview/dexs"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


async def _fetch_dex_24h_usd(client: httpx.AsyncClient) -> Optional[float]:
    try:
        # The default response includes a giant time series we don't need;
        # excluding it keeps the payload small and the call fast.
        r = await client.get(
            DEFILLAMA_DEXS_URL,
            params={
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
            },
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("market_volume_ratio_dex_fetch_failed", error=str(e))
        return None
    total = payload.get("total24h")
    if total is None:
        return None
    try:
        return float(total)
    except (TypeError, ValueError):
        return None


async def _fetch_total_24h_usd(client: httpx.AsyncClient) -> Optional[float]:
    try:
        r = await client.get(COINGECKO_GLOBAL_URL)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("market_volume_ratio_cg_global_fetch_failed", error=str(e))
        return None
    data = payload.get("data") or {}
    tv = data.get("total_volume") or {}
    usd = tv.get("usd")
    if usd is None:
        return None
    try:
        return float(usd)
    except (TypeError, ValueError):
        return None


async def collect_market_volume_ratio(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+market volume ratio)"},
    ) as client:
        dex_usd = await _fetch_dex_24h_usd(client)
        total_usd = await _fetch_total_24h_usd(client)

    if dex_usd is None or total_usd is None or total_usd <= 0:
        log.warning(
            "market_volume_ratio_skipped",
            dex_usd=dex_usd, total_usd=total_usd,
        )
        return

    # CoinGecko's "total_volume" already includes DEX activity reported
    # to them, so dex_share = dex / total is the closest free
    # approximation (slightly underestimates DEX share). We accept that
    # — the signal we care about is the trend, not absolute precision.
    dex_share_pct = (dex_usd / total_usd) * 100.0

    now = datetime.now(timezone.utc)
    inserted = 0
    for metric, value in (
        ("dex_volume_24h_usd",    dex_usd),
        ("total_volume_24h_usd",  total_usd),
        ("dex_share_pct",         dex_share_pct),
    ):
        pid = await db.insert_onchain_metric(
            coin="MARKET",
            metric=metric,
            value=value,
            source="defillama+coingecko",
            observed_at=now,
            raw={},
        )
        if pid is not None:
            inserted += 1

    log.info(
        "market_volume_ratio_collected",
        dex_usd=dex_usd,
        total_usd=total_usd,
        dex_share_pct=dex_share_pct,
        inserted=inserted,
    )
