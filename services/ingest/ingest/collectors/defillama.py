"""
DefiLlama TVL collector.

Pulls current Total Value Locked per chain. TVL is an on-chain proxy for
DeFi demand: rising TVL on a chain often precedes price strength for the
chain's native token.

Free public API, no key required. We map each chain to the native token
we already track (e.g. Ethereum chain TVL → coin='ETH', metric='chain_tvl').
"""
from datetime import datetime, timezone

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

URL = "https://api.llama.fi/v2/chains"

# DefiLlama "name" field → tracked symbol. Only chains whose native token
# is in `coins.TRACKED` are recorded; everything else is ignored.
CHAIN_TO_COIN: dict[str, str] = {
    "Ethereum":   "ETH",
    "BSC":        "BNB",
    "Solana":     "SOL",
    "Tron":       "TRX",
    "Avalanche":  "AVAX",
    "Polygon":    "MATIC",
    "Arbitrum":   "ARB",
    "Optimism":   "OP",
    "Bitcoin":    "BTC",
    "Aptos":      "APT",
    "Sui":        "SUI",
    "Near":       "NEAR",
    "Mantle":     "MNT",
    "Sei":        "SEI",
    "Injective":  "INJ",
    "Cardano":    "ADA",
    "Polkadot":   "DOT",
    "Cosmos":     "ATOM",
}


async def collect_defillama(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+defillama)"},
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        chains = resp.json()

    now = datetime.now(timezone.utc)
    inserted = 0
    matched = 0
    for chain in chains:
        name = chain.get("name")
        coin = CHAIN_TO_COIN.get(name)
        if coin is None:
            continue
        tvl = chain.get("tvl")
        if tvl is None:
            continue
        matched += 1
        pid = await db.insert_onchain_metric(
            coin=coin,
            metric="chain_tvl",
            value=float(tvl),
            source="defillama",
            observed_at=now,
            raw={"chain": name, "tokenSymbol": chain.get("tokenSymbol")},
        )
        if pid is not None:
            inserted += 1

    log.info(
        "defillama_collected",
        chains_total=len(chains),
        chains_matched=matched,
        inserted=inserted,
    )
