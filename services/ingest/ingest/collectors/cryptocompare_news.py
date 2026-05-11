"""
CryptoCompare news collector.

Free public endpoint, no API key required. Returns up to ~50 articles per
call from a wide spread of crypto news sources. Articles flow into the
existing `posts` table and are scored by CryptoBERT like every other text
source.
"""
from datetime import datetime, timezone

import httpx
import structlog

from ..coins import detect_coin
from ..db import DB
from ..settings import settings

log = structlog.get_logger()

NEWS_URL = "https://min-api.cryptocompare.com/data/v2/news/"


async def collect_cryptocompare_news(db: DB) -> None:
    params = {"lang": "EN", "sortOrder": "latest"}
    if settings.cryptocompare_api_key:
        params["api_key"] = settings.cryptocompare_api_key
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "crypto-bot/0.1 (+cryptocompare news)"},
    ) as client:
        resp = await client.get(NEWS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    if data.get("Response") == "Error":
        log.warning(
            "cryptocompare_error",
            message=data.get("Message"),
            has_key=bool(settings.cryptocompare_api_key),
        )
        return

    articles = data.get("Data", [])
    inserted = 0
    for art in articles:
        title = (art.get("title") or "").strip()
        body = (art.get("body") or "").strip()
        text = f"{title}\n\n{body}".strip()
        if not text:
            continue
        coin = detect_coin(text)
        if coin is None:
            continue
        article_id = art.get("id") or art.get("guid") or art.get("url")
        if not article_id:
            continue
        published = art.get("published_on")  # epoch seconds
        try:
            posted_at = (
                datetime.fromtimestamp(int(published), tz=timezone.utc)
                if published is not None
                else datetime.now(timezone.utc)
            )
        except (TypeError, ValueError):
            posted_at = datetime.now(timezone.utc)
        pid = await db.insert_post(
            source="cryptocompare",
            source_id=str(article_id),
            coin=coin,
            text=text[:8000],
            author=art.get("source") or art.get("source_info", {}).get("name"),
            url=art.get("url"),
            posted_at=posted_at,
            raw={"title": title, "tags": art.get("tags"), "categories": art.get("categories")},
        )
        if pid is not None:
            inserted += 1

    log.info("cryptocompare_collected", inserted=inserted, seen=len(articles))
