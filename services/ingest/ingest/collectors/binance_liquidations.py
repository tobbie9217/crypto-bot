"""
Binance Futures liquidations via WebSocket (free, no key).

Subscribes to the public `!forceOrder@arr` stream, which broadcasts every
forced position closure across all USDT-M perpetual contracts in
real time. We aggregate USD-notional per coin per side into a 1-minute
buffer and flush to onchain_metrics as two metrics:

  * liquidation_long_usd   — USD value of LONG positions force-closed
  * liquidation_short_usd  — USD value of SHORT positions force-closed

Big liquidation cascades mark capitulation lows (mass long-liq) and
blow-off tops (mass short-liq) — clean signals for a pullback strategy.

Binance side convention:
  msg.o.S == "SELL"  -> a LONG position was liquidated (exchange sells to close)
  msg.o.S == "BUY"   -> a SHORT position was liquidated (exchange buys to close)
"""
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import structlog
import websockets

from ..db import DB

log = structlog.get_logger()

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"

# Drain the buffer this often. 60s matches the resolution we need for
# the 1h rollup downstream while keeping DB writes trivial even mid-cascade.
FLUSH_INTERVAL_S = 60

# Reconnect backoff. Binance idles connections after ~24h; transient
# blips happen far more often.
RECONNECT_BACKOFF_S = 5
MAX_BACKOFF_S = 60

# WebSocket keepalive — Binance sends pings every ~3min; we add our own
# layer so a dead TCP connection gets noticed quickly.
WS_PING_INTERVAL_S = 20
WS_PING_TIMEOUT_S = 10


def _perp_to_base(perp_symbol: str) -> Optional[str]:
    """BTCUSDT -> 'BTC', 1000SHIBUSDT -> 'SHIB'. None if not a USDT perp."""
    if not perp_symbol.endswith("USDT"):
        return None
    base = perp_symbol[:-4]
    if base.startswith("1000"):
        base = base[4:]
    return base or None


class _Buffer:
    """Per-(coin, side) running USD totals + event counts since last flush."""

    def __init__(self) -> None:
        self.usd: dict[tuple[str, str], float] = defaultdict(float)
        self.events: dict[tuple[str, str], int] = defaultdict(int)

    def add(self, coin: str, side: str, usd_value: float) -> None:
        key = (coin, side)
        self.usd[key] += usd_value
        self.events[key] += 1

    def drain(self) -> dict[tuple[str, str], tuple[float, int]]:
        snapshot = {k: (self.usd[k], self.events[k]) for k in self.usd}
        self.usd.clear()
        self.events.clear()
        return snapshot


async def _flusher(db: DB, buffer: _Buffer) -> None:
    """Drain the buffer every FLUSH_INTERVAL_S and write totals to DB.

    Only writes coins that actually saw a liquidation in the bucket —
    zero rows for the long tail of quiet coins keeps the table sparse.
    """
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_S)
        try:
            snapshot = buffer.drain()
            if not snapshot:
                continue
            now = datetime.now(timezone.utc)
            inserted = 0
            for (coin, position_side), (usd, events) in snapshot.items():
                metric = f"liquidation_{position_side}_usd"
                pid = await db.insert_onchain_metric(
                    coin=coin, metric=metric, value=usd,
                    source="binance_futures", observed_at=now,
                    raw={"events": events},
                )
                if pid is not None:
                    inserted += 1
            log.info(
                "binance_liquidations_flushed",
                distinct_coins=len({k[0] for k in snapshot}),
                inserted=inserted,
            )
        except Exception as e:  # noqa: BLE001 — flusher must never die
            log.warning("binance_liquidations_flush_failed", error=str(e))


async def run_binance_liquidations_listener(db: DB) -> None:
    buffer = _Buffer()
    flusher_task = asyncio.create_task(_flusher(db, buffer))
    backoff = RECONNECT_BACKOFF_S
    try:
        while True:
            try:
                async with websockets.connect(
                    WS_URL,
                    ping_interval=WS_PING_INTERVAL_S,
                    ping_timeout=WS_PING_TIMEOUT_S,
                ) as ws:
                    log.info("binance_liquidations_connected", url=WS_URL)
                    backoff = RECONNECT_BACKOFF_S
                    async for raw_msg in ws:
                        try:
                            msg = json.loads(raw_msg)
                            order = msg.get("o", {})
                            sym = order.get("s")
                            side = order.get("S")
                            qty = order.get("q")
                            # `ap` (average filled price) is more accurate than
                            # `p` (limit price) but only present when status=FILLED.
                            price = order.get("ap") or order.get("p")
                            if not sym or not side or qty is None or price is None:
                                continue
                            base = _perp_to_base(sym)
                            if base is None:
                                continue
                            try:
                                usd_value = float(qty) * float(price)
                            except (TypeError, ValueError):
                                continue
                            position_side = "long" if side == "SELL" else "short"
                            buffer.add(base, position_side, usd_value)
                        except Exception as e:  # noqa: BLE001
                            log.warning("binance_liquidation_parse_failed", error=str(e))
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "binance_liquidations_ws_disconnected",
                    error=str(e),
                    backoff_s=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
    finally:
        flusher_task.cancel()
        try:
            await flusher_task
        except asyncio.CancelledError:
            pass
