"""
Reddit subreddit RSS collector.

Reads `https://www.reddit.com/r/<sub>/new.rss` for each configured
subreddit. The public RSS endpoint requires no API credentials, just a
distinct User-Agent. Reuses `detect_coin` and the `REDDIT_SUBREDDITS`
env list from the original asyncpraw collector.
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


def _entry_timestamp(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


async def collect_reddit_rss(db: DB) -> None:
    inserted = 0
    seen = 0
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        # Reddit blocks default httpx UA; a unique one is enough.
        headers={"User-Agent": settings.reddit_user_agent},
    ) as client:
        for sub in settings.reddit_subreddits_list:
            url = f"https://www.reddit.com/r/{sub}/new.rss"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("reddit_rss_fetch_failed", subreddit=sub, error=str(e))
                continue
            parsed = feedparser.parse(resp.content)
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
                author = entry.get("author")
                pid = await db.insert_post(
                    source="reddit_rss",
                    source_id=str(source_id),
                    coin=coin,
                    text=text[:8000],
                    author=str(author) if author else sub,
                    url=entry.get("link"),
                    posted_at=_entry_timestamp(entry),
                    raw={"subreddit": sub, "title": title},
                )
                if pid is not None:
                    inserted += 1
    log.info(
        "reddit_rss_collected",
        inserted=inserted,
        seen=seen,
        subs=len(settings.reddit_subreddits_list),
    )
