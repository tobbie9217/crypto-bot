"""
Crypto Fear & Greed Index collector.

Pulls the daily 0–100 sentiment gauge from alternative.me. Free, no auth.
Stored as a market-wide signal under the sentinel coin 'MARKET' so the
strategy can reference it independently of per-coin metrics.

Classic contrarian signal:
  * Below 20  ("Extreme Fear")  → historical accumulation zone
  * Above 80  ("Extreme Greed") → historical distribution zone
"""
from datetime import datetime, timezone

import httpx
import structlog

from ..db import DB

log = structlog.get_logger()

URL = "https://api.alternative.me/fng/?limit=1"


async def collect_fear_greed(db: DB) -> None:
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+fear greed)"},
    ) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
        payload = resp.json()

    data = payload.get("data") or []
    if not data:
        log.warning("fear_greed_empty_payload")
        return

    latest = data[0]
    try:
        value = float(latest["value"])
    except (KeyError, TypeError, ValueError):
        log.warning("fear_greed_parse_failed", payload=latest)
        return

    ts = latest.get("timestamp")
    try:
        observed_at = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        observed_at = datetime.now(timezone.utc)

    pid = await db.insert_onchain_metric(
        coin="MARKET",
        metric="fear_greed_index",
        value=value,
        source="alternative.me",
        observed_at=observed_at,
        raw={"classification": latest.get("value_classification")},
    )
    log.info(
        "fear_greed_collected",
        value=value,
        classification=latest.get("value_classification"),
        inserted=pid is not None,
    )
