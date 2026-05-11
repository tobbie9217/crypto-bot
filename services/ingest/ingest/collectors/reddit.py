from datetime import datetime, timezone

import asyncpraw
import structlog

from ..coins import detect_coin  # re-exported for backwards-compat callers
from ..db import DB
from ..settings import settings

log = structlog.get_logger()

__all__ = ["detect_coin", "collect_reddit"]


async def collect_reddit(db: DB) -> None:
    reddit = asyncpraw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
    )

    inserted = 0
    seen = 0
    try:
        for sub_name in settings.reddit_subreddits_list:
            sub = await reddit.subreddit(sub_name)
            async for post in sub.new(limit=25):
                seen += 1
                text = f"{post.title}\n\n{post.selftext or ''}".strip()
                coin = detect_coin(text)
                if coin is None:
                    continue
                pid = await db.insert_post(
                    source="reddit",
                    source_id=post.id,
                    coin=coin,
                    text=text[:8000],
                    author=str(post.author) if post.author else None,
                    url=f"https://reddit.com{post.permalink}",
                    posted_at=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                    raw={
                        "score": post.score,
                        "num_comments": post.num_comments,
                        "subreddit": sub_name,
                    },
                )
                if pid is not None:
                    inserted += 1
    finally:
        await reddit.close()

    log.info("reddit_collected", seen=seen, inserted=inserted)
