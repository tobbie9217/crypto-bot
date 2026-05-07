from datetime import datetime, timezone

import httpx
import structlog

from ..db import DB
from ..settings import settings

log = structlog.get_logger()

# LunarCrush API4 endpoint shape. Plans below "Builder" may not have post-level
# access — in that case the call returns 403 and we fall back silently. Adjust
# the URL if your plan exposes a different path.
ENDPOINT = "https://lunarcrush.com/api4/public/coins/{coin}/posts/v1"


async def collect_lunarcrush(db: DB) -> None:
    headers = {"Authorization": f"Bearer {settings.lunarcrush_token}"}
    inserted_total = 0

    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        for coin in settings.coins_list:
            resp = await client.get(ENDPOINT.format(coin=coin))
            if resp.status_code in (401, 403):
                log.warning("lunarcrush_access_denied", coin=coin, status=resp.status_code)
                return
            if resp.status_code == 429:
                log.warning("lunarcrush_rate_limited", coin=coin)
                continue
            resp.raise_for_status()
            data = resp.json().get("data", []) or []

            inserted = 0
            for item in data:
                text = item.get("post_title") or item.get("post_summary") or ""
                if not text:
                    continue
                ts = item.get("post_created")
                if not ts:
                    continue
                pid = await db.insert_post(
                    source="lunarcrush",
                    source_id=str(item.get("id", "")),
                    coin=coin,
                    text=text[:8000],
                    author=item.get("creator_name"),
                    url=item.get("post_link"),
                    posted_at=datetime.fromtimestamp(ts, tz=timezone.utc),
                    raw=item,
                )
                if pid is not None:
                    inserted += 1

            inserted_total += inserted
            log.info("lunarcrush_coin_collected", coin=coin, inserted=inserted, total=len(data))

    log.info("lunarcrush_run_done", inserted=inserted_total)
