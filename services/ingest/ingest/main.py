import asyncio
import logging
from datetime import datetime

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .collectors.binance_derivatives import collect_binance_derivatives
from .collectors.binance_listings import collect_binance_listings
from .collectors.binance_ratios import collect_binance_ratios
from .collectors.binance_spot import collect_binance_spot
from .collectors.cryptocompare_news import collect_cryptocompare_news
from .collectors.cryptopanic import collect_cryptopanic
from .collectors.defillama import collect_defillama
from .collectors.fear_greed import collect_fear_greed
from .collectors.lunarcrush import collect_lunarcrush
from .collectors.news_rss import collect_news_rss
from .collectors.reddit import collect_reddit
from .collectors.reddit_rss import collect_reddit_rss
from .collectors.telegram_channels import run_telegram_listener
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

    # RSS-based collectors run unconditionally — no credentials required.
    scheduler.add_job(
        safe_run, "interval", seconds=settings.news_rss_interval_s,
        args=["news_rss", collect_news_rss, db], next_run_time=now,
    )
    log.info("scheduled", collector="news_rss", interval_s=settings.news_rss_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.reddit_rss_interval_s,
        args=["reddit_rss", collect_reddit_rss, db], next_run_time=now,
    )
    log.info("scheduled", collector="reddit_rss", interval_s=settings.reddit_rss_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.cryptocompare_news_interval_s,
        args=["cryptocompare_news", collect_cryptocompare_news, db], next_run_time=now,
    )
    log.info("scheduled", collector="cryptocompare_news", interval_s=settings.cryptocompare_news_interval_s)

    # Binance spot ticker replaces CoinGecko market (faster, higher rate-limit,
    # exchange-native data). The coingecko_market.py file is kept for reference
    # but no longer scheduled.
    scheduler.add_job(
        safe_run, "interval", seconds=settings.binance_spot_interval_s,
        args=["binance_spot", collect_binance_spot, db], next_run_time=now,
    )
    log.info("scheduled", collector="binance_spot", interval_s=settings.binance_spot_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.defillama_interval_s,
        args=["defillama", collect_defillama, db], next_run_time=now,
    )
    log.info("scheduled", collector="defillama", interval_s=settings.defillama_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.binance_derivatives_interval_s,
        args=["binance_derivatives", collect_binance_derivatives, db], next_run_time=now,
    )
    log.info("scheduled", collector="binance_derivatives", interval_s=settings.binance_derivatives_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.binance_ratios_interval_s,
        args=["binance_ratios", collect_binance_ratios, db], next_run_time=now,
    )
    log.info("scheduled", collector="binance_ratios", interval_s=settings.binance_ratios_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.binance_listings_interval_s,
        args=["binance_listings", collect_binance_listings, db], next_run_time=now,
    )
    log.info("scheduled", collector="binance_listings", interval_s=settings.binance_listings_interval_s)

    scheduler.add_job(
        safe_run, "interval", seconds=settings.fear_greed_interval_s,
        args=["fear_greed", collect_fear_greed, db], next_run_time=now,
    )
    log.info("scheduled", collector="fear_greed", interval_s=settings.fear_greed_interval_s)

    scheduler.start()

    # Telegram is long-running rather than scheduled — runs as its own task.
    telegram_task = asyncio.create_task(safe_run("telegram", run_telegram_listener, db))

    try:
        await asyncio.Event().wait()
    finally:
        telegram_task.cancel()
        scheduler.shutdown()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
