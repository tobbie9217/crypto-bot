"""
Crypto news RSS collector.

Pulls public RSS feeds from major crypto news sites — no API key, no signup.
Each entry's title + summary is run through `detect_coin`; matches land in
the `posts` table with `source='news_rss'`.

Default feeds can be overridden via the `NEWS_RSS_FEEDS` env var
(comma-separated URLs).
"""
import calendar
from datetime import datetime, timezone

import feedparser
import httpx
import structlog

from ..db import DB
from ..settings import settings
from .reddit import detect_coin

log = structlog.get_logger()

DEFAULT_FEEDS: tuple[str, ...] = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblock.co/rss.xml",
    "https://beincrypto.com/feed/",
)


def _entry_timestamp(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return datetime.now(timezone.utc)
    # feedparser normalizes time tuples to UTC.
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


async def collect_news_rss(db: DB) -> None:
    feeds = settings.news_rss_feeds_list or list(DEFAULT_FEEDS)
    inserted = 0
    seen = 0
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": "crypto-bot/0.1 (+rss reader)"},
    ) as client:
        for feed_url in feeds:
            try:
                resp = await client.get(feed_url)
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("news_rss_fetch_failed", feed=feed_url, error=str(e))
                continue
            parsed = feedparser.parse(resp.content)
            feed_title = parsed.feed.get("title") if hasattr(parsed.feed, "get") else feed_url
            for entry in parsed.entries:
                seen += 1
                title = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "").strip()
                text = f"{title}\n\n{summary}".strip()
                if not text:
                    continue
                coin = detect_coin(text)
                if coin is None:
                    continue
                source_id = entry.get("id") or entry.get("link")
                if not source_id:
                    continue
                pid = await db.insert_post(
                    source="news_rss",
                    source_id=str(source_id),
                    coin=coin,
                    text=text[:8000],
                    author=feed_title,
                    url=entry.get("link"),
                    posted_at=_entry_timestamp(entry),
                    raw={"feed": feed_url, "title": title},
                )
                if pid is not None:
                    inserted += 1
    log.info("news_rss_collected", inserted=inserted, seen=seen, feeds=len(feeds))
