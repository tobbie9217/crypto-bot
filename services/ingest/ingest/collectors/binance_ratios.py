"""
Binance Futures positioning ratios for ALL active perps.

  * top_trader_lsr        — top-trader account long/short ratio ("smart money")
  * global_account_lsr    — all-account long/short ratio (retail proxy)
  * taker_buy_sell_ratio  — aggressive buy vs sell volume over 1h

Three endpoints × N perps. Binance allows ~2400 req/min unauth so even
~330 perps × 3 = 990 calls fits comfortably in one cycle.

Top-trader LSR diverging from global LSR is a textbook smart-money vs
retail disagreement signal — Glassnode/CryptoQuant charge $30+/mo for
exactly this; here it's free.
"""
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

# (endpoint, metric_name, value_field_in_response)
ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("https://fapi.binance.com/futures/data/topLongShortAccountRatio",   "top_trader_lsr",       "longShortRatio"),
    ("https://fapi.binance.com/futures/data/globalLongShortAccountRatio","global_account_lsr",   "longShortRatio"),
    ("https://fapi.binance.com/futures/data/takerlongshortRatio",        "taker_buy_sell_ratio", "buySellRatio"),
)


def _perp_to_base(perp_symbol: str) -> Optional[str]:
    if not perp_symbol.endswith("USDT"):
        return None
    base = perp_symbol[:-4]
    if base.startswith("1000"):
        base = base[4:]
    return base or None


async def collect_binance_ratios(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+binance ratios)"},
    ) as client:
        # exchangeInfo is the authoritative TRADING set; avoids 400s for
        # delisted perps that linger in premiumIndex.
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
            log.warning("binance_ratios_exchange_info_failed", error=str(e))
            return

        inserted = 0
        seen = 0
        for sym, base in live_perps.items():
            for url, metric, value_key in ENDPOINTS:
                try:
                    r = await client.get(
                        url, params={"symbol": sym, "period": "1h", "limit": 1}
                    )
                    r.raise_for_status()
                    rows = r.json()
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "binance_ratio_fetch_failed",
                        symbol=sym, metric=metric, error=str(e),
                    )
                    continue
                if not rows:
                    continue
                row = rows[0]
                raw_value = row.get(value_key)
                ts = row.get("timestamp")
                if raw_value is None or ts is None:
                    continue
                try:
                    value = float(raw_value)
                    observed_at = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                except (TypeError, ValueError):
                    continue
                seen += 1
                pid = await db.insert_onchain_metric(
                    coin=base,
                    metric=metric,
                    value=value,
                    source="binance_futures",
                    observed_at=observed_at,
                    raw={"symbol": sym},
                )
                if pid is not None:
                    inserted += 1

    log.info("binance_ratios_collected", perps=len(live_perps), seen=seen, inserted=inserted)
