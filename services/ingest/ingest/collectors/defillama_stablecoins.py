"""
DefiLlama stablecoin supply collector — free, no key.

Writes two flavours of supply metric into onchain_metrics:

  1. Market aggregate (coin='MARKET', metric='stablecoin_supply_usd')
     — total USD-pegged stablecoin float across every tracked issuer.
     "Dry powder" sitting in stablecoins is a leading indicator of
     buying pressure; net minting often precedes rallies.

  2. Per-issuer supply (coin=symbol, metric='stablecoin_supply') for
     the largest stablecoins (USDT, USDC, DAI, FDUSD, USDe, ...).
     Lets us watch issuer-specific events (e.g. USDT minting waves)
     separately from the aggregate.

DefiLlama exposes a single REST call that returns every pegged asset's
current circulating supply by peg type — we sum the USD-pegged ones.
"""
from datetime import datetime, timezone

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

URL = "https://stablecoins.llama.fi/stablecoins"

# Issuers we record on their own. Anything else is rolled into the
# MARKET aggregate but not stored under its own coin row, to keep the
# table compact. Symbol must match what DefiLlama returns in `symbol`.
TRACKED_ISSUERS: frozenset[str] = frozenset({
    "USDT", "USDC", "DAI", "FDUSD", "USDe", "PYUSD", "TUSD", "BUSD", "USDD",
})


async def collect_defillama_stablecoins(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+defillama stablecoins)"},
    ) as client:
        try:
            resp = await client.get(URL)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("defillama_stablecoins_fetch_failed", error=str(e))
            return

    assets = payload.get("peggedAssets") or []
    if not assets:
        log.warning("defillama_stablecoins_empty_payload")
        return

    now = datetime.now(timezone.utc)
    inserted = 0
    market_total = 0.0
    issuer_rows = 0

    for asset in assets:
        peg_type = asset.get("pegType")
        # Only USD-pegged stablecoins go into the aggregate. EUR/CNY-pegged
        # supply is rounding error today (<<1%) and dilutes the signal.
        if peg_type != "peggedUSD":
            continue
        circ = asset.get("circulating") or {}
        usd = circ.get("peggedUSD")
        if usd is None:
            continue
        try:
            usd = float(usd)
        except (TypeError, ValueError):
            continue
        market_total += usd

        symbol = (asset.get("symbol") or "").upper()
        if symbol in TRACKED_ISSUERS:
            pid = await db.insert_onchain_metric(
                coin=symbol,
                metric="stablecoin_supply",
                value=usd,
                source="defillama",
                observed_at=now,
                raw={"name": asset.get("name"), "pegType": peg_type},
            )
            if pid is not None:
                inserted += 1
                issuer_rows += 1

    if market_total > 0:
        pid = await db.insert_onchain_metric(
            coin="MARKET",
            metric="stablecoin_supply_usd",
            value=market_total,
            source="defillama",
            observed_at=now,
            raw={"issuers_counted": len(assets)},
        )
        if pid is not None:
            inserted += 1

    log.info(
        "defillama_stablecoins_collected",
        market_total_usd=market_total,
        issuers_recorded=issuer_rows,
        inserted=inserted,
    )
