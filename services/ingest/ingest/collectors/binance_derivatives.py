"""
Binance Futures derivatives — funding rate, mark price, open interest,
and forward-looking funding predictions.

Pulls EVERY USDT-M perpetual contract on Binance, not just curated coins.
Funding + mark + index + predicted-funding come from one bulk call;
open interest is per-symbol.

Forward-looking funding signals (added per deep_research.pdf Part 1 gap):

  * index_price          — spot-fair price ref (Binance's internal index)
  * premium_index_pct    — (mark - index) / index, the live premium of
                           perp over spot. Strongest leading indicator
                           of where the NEXT funding settlement will land.
  * funding_rate_predicted — Binance's actual next-funding formula:
        clamp(premium + clamp(interest - premium, -0.05%, +0.05%),
              -0.75%, +0.75%)
    Note this uses the INSTANTANEOUS premium, not the 1h TWAP Binance
    will use at settlement — so it's a noisy forward estimate, but
    directionally correct and free.

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

# Funding clamps per Binance docs. The premium-vs-interest difference is
# clamped to ±0.05%; the final funding rate to ±0.75% for most USDT-M
# pairs. A handful of low-liquidity perps use ±2% — accepting some
# inaccuracy on those rather than maintaining a per-symbol cap table.
_DAMP_CAP    = 0.0005
_FUNDING_CAP = 0.0075


def _predict_funding(premium: float, interest: float) -> float:
    """Apply Binance's funding-rate formula to a (premium, interest) pair."""
    diff = max(-_DAMP_CAP, min(_DAMP_CAP, interest - premium))
    raw = premium + diff
    return max(-_FUNDING_CAP, min(_FUNDING_CAP, raw))


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
            index = row.get("indexPrice")
            interest_raw = row.get("interestRate")
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

            # --- Forward-looking funding signals ---
            # Need both mark + index (and a non-zero index) to compute the
            # live premium. If either is missing we skip silently — these
            # are derived metrics, not authoritative observations.
            try:
                mark_f = float(mark) if mark is not None else None
                index_f = float(index) if index is not None else None
            except (TypeError, ValueError):
                mark_f = index_f = None
            if mark_f is not None and index_f is not None and index_f > 0:
                premium = (mark_f - index_f) / index_f
                # `interestRate` is the per-funding-interval rate (decimal),
                # e.g. 0.0001 = 0.01% per 8h. Default to Binance's standard
                # 0.01% if the field is missing — true for some new perps.
                try:
                    interest = float(interest_raw) if interest_raw is not None else 0.0001
                except (TypeError, ValueError):
                    interest = 0.0001
                predicted = _predict_funding(premium, interest)

                pid = await db.insert_onchain_metric(
                    coin=base, metric="index_price", value=index_f,
                    source="binance_futures", observed_at=now,
                    raw={"symbol": sym},
                )
                if pid is not None:
                    inserted += 1
                pid = await db.insert_onchain_metric(
                    coin=base, metric="premium_index_pct", value=premium * 100.0,
                    source="binance_futures", observed_at=now,
                    raw={"symbol": sym, "mark": mark_f, "index": index_f},
                )
                if pid is not None:
                    inserted += 1
                pid = await db.insert_onchain_metric(
                    coin=base, metric="funding_rate_predicted", value=predicted,
                    source="binance_futures", observed_at=now,
                    raw={
                        "symbol": sym,
                        "premium": premium,
                        "interest": interest,
                        "next_funding_time": row.get("nextFundingTime"),
                    },
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
