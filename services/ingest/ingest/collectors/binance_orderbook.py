"""
Binance Futures order-book depth snapshot — free, no key.

For every active USDT-M perp we pull the top of book via `/fapi/v1/depth`,
then compute three derived metrics for the slice within 1% of mid:

  * orderbook_bid_depth_usd    — total USD notional resting on the bid side
  * orderbook_ask_depth_usd    — total USD notional resting on the ask side
  * orderbook_imbalance_1pct   — bid_depth / (bid_depth + ask_depth)
                                   0.5 = balanced, >0.5 = bid-heavy (buy walls),
                                   <0.5 = ask-heavy (sell walls / absorption).

Order-book imbalance flags whale walls and absorption — the deep-research
audit calls it the single biggest missing piece for short-term entries.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"

# 100 levels comfortably covers ±1% of mid for nearly every liquid pair.
DEPTH_LIMIT = 100

# Window around mid that we treat as "near book". 1% catches whale walls
# without burning compute on the deep illiquid tail.
DEPTH_PCT_WINDOW = 0.01

# Sleep between per-symbol calls. With ~330 perps and ~180ms target, this
# spreads ~330 calls over ~60s — well under Binance's 2400/min unauth limit.
PER_CALL_SLEEP_S = 0.05


def _perp_to_base(perp_symbol: str) -> Optional[str]:
    """BTCUSDT -> 'BTC', 1000SHIBUSDT -> 'SHIB'. None if not a USDT perp."""
    if not perp_symbol.endswith("USDT"):
        return None
    base = perp_symbol[:-4]
    if base.startswith("1000"):
        base = base[4:]
    return base or None


def _depth_within(levels: list[list[str]], min_price: float, max_price: float) -> float:
    """Sum USD-notional (price * qty) for levels with price in [min, max]."""
    total = 0.0
    for level in levels:
        try:
            price = float(level[0])
            qty = float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if min_price <= price <= max_price:
            total += price * qty
    return total


async def collect_binance_orderbook(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+binance orderbook)"},
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
            log.warning("binance_orderbook_exchange_info_failed", error=str(e))
            return

        inserted = 0
        seen = 0
        skipped_empty = 0
        for sym, base in live_perps.items():
            try:
                r = await client.get(DEPTH_URL, params={"symbol": sym, "limit": DEPTH_LIMIT})
                r.raise_for_status()
                book = r.json()
            except Exception as e:  # noqa: BLE001
                log.warning("binance_orderbook_fetch_failed", symbol=sym, error=str(e))
                # Light rate-limit guard even on failure so a sustained 429
                # storm doesn't tighten the loop.
                await asyncio.sleep(PER_CALL_SLEEP_S)
                continue

            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids or not asks:
                skipped_empty += 1
                await asyncio.sleep(PER_CALL_SLEEP_S)
                continue

            try:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
            except (TypeError, ValueError, IndexError):
                skipped_empty += 1
                await asyncio.sleep(PER_CALL_SLEEP_S)
                continue
            mid = (best_bid + best_ask) / 2.0
            if mid <= 0:
                skipped_empty += 1
                await asyncio.sleep(PER_CALL_SLEEP_S)
                continue

            min_bid_price = mid * (1.0 - DEPTH_PCT_WINDOW)
            max_ask_price = mid * (1.0 + DEPTH_PCT_WINDOW)
            bid_usd = _depth_within(bids, min_bid_price, mid)
            ask_usd = _depth_within(asks, mid, max_ask_price)
            total = bid_usd + ask_usd
            if total <= 0:
                skipped_empty += 1
                await asyncio.sleep(PER_CALL_SLEEP_S)
                continue
            imbalance = bid_usd / total

            now = datetime.now(timezone.utc)
            seen += 1
            for metric, value in (
                ("orderbook_bid_depth_usd",   bid_usd),
                ("orderbook_ask_depth_usd",   ask_usd),
                ("orderbook_imbalance_1pct",  imbalance),
            ):
                pid = await db.insert_onchain_metric(
                    coin=base, metric=metric, value=value,
                    source="binance_futures", observed_at=now,
                    raw={"symbol": sym, "mid": mid},
                )
                if pid is not None:
                    inserted += 1

            await asyncio.sleep(PER_CALL_SLEEP_S)

    log.info(
        "binance_orderbook_collected",
        perps=len(live_perps),
        seen=seen,
        skipped_empty=skipped_empty,
        inserted=inserted,
    )
