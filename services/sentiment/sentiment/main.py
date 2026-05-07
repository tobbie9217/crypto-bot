import asyncio
import logging

import asyncpg
import structlog

from .aggregator import compute_aggregates
from .model import CryptoBERT
from .settings import settings


def configure_logging() -> None:
    logging.basicConfig(level=settings.log_level)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


async def fetch_unscored(pool: asyncpg.Pool, batch_size: int) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT p.id, p.text
            FROM posts p
            LEFT JOIN sentiment_scores s
                ON s.post_id = p.id AND s.model = 'cryptobert'
            WHERE s.id IS NULL
            ORDER BY p.id
            LIMIT $1
            """,
            batch_size,
        )


async def write_scores(
    pool: asyncpg.Pool,
    rows: list[asyncpg.Record],
    results: list[tuple[float, str, float]],
) -> None:
    payload = [
        (rows[i]["id"], score, label, confidence)
        for i, (score, label, confidence) in enumerate(results)
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO sentiment_scores (post_id, model, score, label, confidence)
            VALUES ($1, 'cryptobert', $2, $3, $4)
            ON CONFLICT (post_id, model) DO NOTHING
            """,
            payload,
        )


async def main() -> None:
    configure_logging()
    log = structlog.get_logger()

    log.info("loading_model", name=settings.model_name)
    model = CryptoBERT(settings.model_name, max_tokens=settings.max_text_tokens)

    log.info("connecting_db")
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=3)

    try:
        while True:
            rows = await fetch_unscored(pool, settings.batch_size)
            if rows:
                texts = [r["text"] for r in rows]
                results = model.score_batch(texts)
                await write_scores(pool, rows, results)
                await compute_aggregates(pool)
                log.info("scored", count=len(rows))
            else:
                await asyncio.sleep(settings.idle_sleep_s)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
