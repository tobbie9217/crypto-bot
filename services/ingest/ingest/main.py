import asyncio
import logging
from datetime import datetime

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .collectors.cryptopanic import collect_cryptopanic
from .collectors.lunarcrush import collect_lunarcrush
from .collectors.reddit import collect_reddit
from .db import DB
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


async def safe_run(name: str, fn, *args) -> None:
    log = structlog.get_logger().bind(collector=name)
    try:
        await fn(*args)
    except Exception as e:  # noqa: BLE001 — we never want a collector crash to kill the loop
        log.error("collector_error", error=str(e), error_type=type(e).__name__)


async def main() -> None:
    configure_logging()
    log = structlog.get_logger()

    db = DB(settings.database_url)
    await db.connect()
    log.info("db_connected")

    scheduler = AsyncIOScheduler()
    now = datetime.now()

    if settings.reddit_client_id and settings.reddit_client_secret:
        scheduler.add_job(
            safe_run, "interval", seconds=settings.reddit_interval_s,
            args=["reddit", collect_reddit, db], next_run_time=now,
        )
        log.info("scheduled", collector="reddit", interval_s=settings.reddit_interval_s)
    else:
        log.warning("reddit_disabled", reason="missing_credentials")

    if settings.cryptopanic_token:
        scheduler.add_job(
            safe_run, "interval", seconds=settings.cryptopanic_interval_s,
            args=["cryptopanic", collect_cryptopanic, db], next_run_time=now,
        )
        log.info("scheduled", collector="cryptopanic", interval_s=settings.cryptopanic_interval_s)
    else:
        log.warning("cryptopanic_disabled", reason="missing_token")

    if settings.lunarcrush_token:
        scheduler.add_job(
            safe_run, "interval", seconds=settings.lunarcrush_interval_s,
            args=["lunarcrush", collect_lunarcrush, db], next_run_time=now,
        )
        log.info("scheduled", collector="lunarcrush", interval_s=settings.lunarcrush_interval_s)
    else:
        log.warning("lunarcrush_disabled", reason="missing_token")

    scheduler.start()

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
