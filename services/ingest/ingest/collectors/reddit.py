from datetime import datetime, timezone

import asyncpraw
import structlog

from ..db import DB
from ..settings import settings

log = structlog.get_logger()

# Cheap first-pass coin tagger. Real ticker disambiguation needs more work
# (e.g. "SOL" vs "sold") — for week 2 this is good enough; we re-tag during
# sentiment aggregation if needed.
TICKER_TERMS: dict[str, tuple[str, ...]] = {
    "BTC": ("btc", "bitcoin", "$btc"),
    "ETH": ("eth", "ethereum", "ether ", "$eth"),
    "SOL": ("solana", "$sol"),
    "BNB": ("bnb", "binance coin", "$bnb"),
}


def detect_coin(text: str) -> str | None:
    lowered = text.lower()
    for coin, terms in TICKER_TERMS.items():
        if any(t in lowered for t in terms):
            return coin
    return None


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
