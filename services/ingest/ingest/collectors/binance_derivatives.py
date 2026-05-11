"""
Binance Futures derivatives — funding rate, mark price, open interest.

Pulls EVERY USDT-M perpetual contract on Binance, not just curated coins.
Funding + mark come from one bulk call; open interest is per-symbol.

For perps with a "1000" prefix (e.g. 1000SHIBUSDT, 1000PEPEUSDT —
contracts denominated in 1000-unit lots), we strip the prefix so the
`coin` column matches the spot symbol ("SHIB", "PEPE").
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"


def _perp_to_base(perp_symbol: str) -> Optional[str]:
    """BTCUSDT -> 'BTC', 1000SHIBUSDT -> 'SHIB'. None if not a USDT perp."""
    if not perp_symbol.endswith("USDT"):
        return None
    base = perp_symbol[:-4]
    if base.startswith("1000"):
        base = base[4:]
    return base or None


async def collect_binance_derivatives(db: DB) -> None:
    inserted = 0
    seen_funding = 0
    seen_oi = 0

    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+binance derivatives)"},
    ) as client:
        # Authoritative listing — premiumIndex includes stale/delisted symbols
        # that 400 on /openInterest. exchangeInfo is the source of truth.
        try:
            resp = await client.get(EXCHANGE_INFO_URL)
            resp.raise_for_status()
            tradeable: set[str] = {
                s["symbol"]
                for s in resp.json().get("symbols", [])
                if s.get("status") == "TRADING"
                and s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
            }
        except Exception as e:  # noqa: BLE001
            log.warning("binance_derivatives_exchange_info_failed", error=str(e))
            return

        # Bulk: funding + mark for every active perp in one call.
        try:
            resp = await client.get(PREMIUM_URL)
            resp.raise_for_status()
            premium = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("binance_premium_fetch_failed", error=str(e))
            premium = []

        now = datetime.now(timezone.utc)
        # Build perp -> base map from what's both in premiumIndex AND
        # actually tradeable per exchangeInfo.
        live_perps: dict[str, str] = {}
        for row in premium:
            sym = row.get("symbol")
            if not sym or sym not in tradeable:
                continue
            base = _perp_to_base(sym)
            if base is None:
                continue
            live_perps[sym] = base

            seen_funding += 1
            funding = row.get("lastFundingRate")
            mark = row.get("markPrice")
            if funding is not None:
                pid = await db.insert_onchain_metric(
                    coin=base, metric="funding_rate", value=float(funding),
                    source="binance_futures", observed_at=now,
                    raw={"symbol": sym, "next_funding_time": row.get("nextFundingTime")},
                )
                if pid is not None:
                    inserted += 1
            if mark is not None:
                pid = await db.insert_onchain_metric(
                    coin=base, metric="mark_price", value=float(mark),
                    source="binance_futures", observed_at=now,
                    raw={"symbol": sym},
                )
                if pid is not None:
                    inserted += 1

        # Per-symbol: open interest.
        for sym, base in live_perps.items():
            try:
                resp = await client.get(OI_URL, params={"symbol": sym})
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:  # noqa: BLE001
                log.warning("binance_oi_fetch_failed", symbol=sym, error=str(e))
                continue
            oi = payload.get("openInterest")
            if oi is None:
                continue
            seen_oi += 1
            pid = await db.insert_onchain_metric(
                coin=base, metric="open_interest", value=float(oi),
                source="binance_futures", observed_at=now,
                raw={"symbol": sym},
            )
            if pid is not None:
                inserted += 1

    log.info(
        "binance_derivatives_collected",
        funding_seen=seen_funding,
        oi_seen=seen_oi,
        inserted=inserted,
    )
