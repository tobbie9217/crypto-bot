import httpx
import structlog
from dateutil.parser import isoparse

from ..db import DB
from ..settings import settings

log = structlog.get_logger()


async def collect_cryptopanic(db: DB) -> None:
    url = "https://cryptopanic.com/api/v1/posts/"
    params = {
        "auth_token": settings.cryptopanic_token,
        "currencies": ",".join(settings.coins_list),
        "kind": "news",
        "public": "true",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    inserted = 0
    for item in data.get("results", []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        currencies = item.get("currencies") or []
        coin = currencies[0]["code"] if currencies else None
        pid = await db.insert_post(
            source="cryptopanic",
            source_id=str(item["id"]),
            coin=coin,
            text=title[:8000],
            url=item.get("url"),
            posted_at=isoparse(item["published_at"]),
            raw=item,
        )
        if pid is not None:
            inserted += 1

    log.info("cryptopanic_collected", inserted=inserted, total=len(data.get("results", [])))
