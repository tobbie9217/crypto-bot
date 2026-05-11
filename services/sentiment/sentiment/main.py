import asyncio
import logging
import signal
import time

import asyncpg
import structlog

from .aggregator import compute_aggregates, compute_onchain_aggregates
from .model import CryptoBERT
from .settings import settings

HEARTBEAT_PATH = "/tmp/healthz"
HEARTBEAT_INTERVAL_S = 30


async def _heartbeat() -> None:
    """Touch HEARTBEAT_PATH every HEARTBEAT_INTERVAL_S seconds for
    the docker healthcheck. See ingest/main.py for the same pattern."""
    log = structlog.get_logger().bind(task="heartbeat")
    while True:
        try:
            with open(HEARTBEAT_PATH, "w") as f:
                f.write(str(time.time()))
        except OSError as e:
            log.warning("heartbeat_write_failed", error=str(e))
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


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

    heartbeat_task = asyncio.create_task(_heartbeat())

    # SIGTERM/SIGINT → flip stop_event so the while-loop below exits
    # on the next iteration instead of being SIGKILLed by Docker.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        while not stop_event.is_set():
            rows = await fetch_unscored(pool, settings.batch_size)
            if rows:
                texts = [r["text"] for r in rows]
                results = model.score_batch(texts)
                await write_scores(pool, rows, results)
                await compute_aggregates(pool)
                log.info("scored", count=len(rows))
            else:
                # Sleep but break early if shutdown was requested mid-sleep.
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=settings.idle_sleep_s
                    )
                except asyncio.TimeoutError:
                    pass
            # On-chain data lands independently of posts, so refresh its
            # rollups on every tick (cheap — pure SQL on small tables).
            await compute_onchain_aggregates(pool)
    finally:
        log.info("shutdown_initiated")
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await pool.close()
        log.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
