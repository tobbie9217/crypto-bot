"""
New-listing detector for Binance.

Polls /api/v3/exchangeInfo (spot) and /fapi/v1/exchangeInfo (futures) on
a slow cadence. Diffs against the most recent snapshot stored in
`bot_events` and emits a `binance_new_listing` row for each symbol that
appeared since the last poll.

Result: any new pair Binance lists shows up as a queryable event row,
and a structured log line you can grep for in real-time.
"""
import json
from datetime import datetime, timezone

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

SPOT_URL = "https://api.binance.com/api/v3/exchangeInfo"
FUTURES_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"


async def _last_snapshot(db: DB, scope: str) -> set[str]:
    assert db.pool is not None
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT payload FROM bot_events
            WHERE event_type = 'binance_universe_snapshot'
              AND payload->>'scope' = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            scope,
        )
    if row is None:
        return set()
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return set(payload.get("symbols", []))


async def _save_snapshot(db: DB, scope: str, symbols: set[str]) -> None:
    assert db.pool is not None
    payload = json.dumps({"scope": scope, "count": len(symbols), "symbols": sorted(symbols)})
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bot_events (event_type, payload)
            VALUES ('binance_universe_snapshot', $1::jsonb)
            """,
            payload,
        )


async def _emit_new_listing(db: DB, scope: str, symbol: str) -> None:
    assert db.pool is not None
    payload = json.dumps({
        "scope": scope,
        "symbol": symbol,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    })
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bot_events (event_type, payload)
            VALUES ('binance_new_listing', $1::jsonb)
            """,
            payload,
        )
    log.info("binance_new_listing", scope=scope, symbol=symbol)


async def _fetch_spot(client: httpx.AsyncClient) -> set[str]:
    resp = await client.get(SPOT_URL)
    resp.raise_for_status()
    data = resp.json()
    # Binance migrated `permissions` -> `permissionSets` (nested list of
    # lists). status=TRADING + quoteAsset=USDT is enough on its own; the
    # SPOT permission filter was redundant for spot-only ingestion anyway.
    return {
        s["symbol"]
        for s in data.get("symbols", [])
        if s.get("status") == "TRADING"
        and s.get("quoteAsset") == "USDT"
    }


async def _fetch_perps(client: httpx.AsyncClient) -> set[str]:
    resp = await client.get(FUTURES_URL)
    resp.raise_for_status()
    data = resp.json()
    return {
        s["symbol"]
        for s in data.get("symbols", [])
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
    }


async def collect_binance_listings(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": "crypto-bot/0.1 (+binance listings)"},
    ) as client:
        try:
            spot_now = await _fetch_spot(client)
        except Exception as e:  # noqa: BLE001
            log.warning("binance_listings_spot_failed", error=str(e))
            spot_now = set()

        try:
            perp_now = await _fetch_perps(client)
        except Exception as e:  # noqa: BLE001
            log.warning("binance_listings_futures_failed", error=str(e))
            perp_now = set()

    new_total = 0
    for scope, current in (("spot", spot_now), ("perp", perp_now)):
        if not current:
            continue
        previous = await _last_snapshot(db, scope)
        # First-ever run: don't blast every existing symbol as "new" — just
        # snapshot and move on. From the next cycle onward we have a baseline.
        if previous:
            for new_sym in sorted(current - previous):
                await _emit_new_listing(db, scope, new_sym)
                new_total += 1
        await _save_snapshot(db, scope, current)

    log.info(
        "binance_listings_collected",
        spot_count=len(spot_now),
        perp_count=len(perp_now),
        new_listings=new_total,
    )
