"""Shared psycopg2 connection pool for sentiment strategies.

Freqtrade strategies run in synchronous code paths, so a thread-safe
psycopg2 pool (rather than asyncpg) is the right fit. The pool is
lazily initialized from DATABASE_URL on first use and reused for the
lifetime of the freqtrade process — avoiding the per-call TCP+TLS+auth
handshake that previously happened on every pair, every candle.

Usage:

    from db_pool import get_conn

    with get_conn() as conn:
        if conn is None:
            return fallback
        with conn.cursor() as cur:
            cur.execute(...)
            row = cur.fetchone()
"""
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg2
import psycopg2.extensions
from psycopg2 import pool as pg_pool

log = logging.getLogger(__name__)

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()

# Rate-limited error reporting: per-operation timestamp of the last
# WARNING line we emitted. With 50+ pairs hitting the DB every candle,
# an unrate-limited warning would flood logs during an outage. One line
# per minute per operation is loud enough to be impossible to miss but
# quiet enough that a 10-minute outage is ~30 log lines total.
_error_state_lock = threading.Lock()
_error_last_log_ts: dict[str, float] = {}
DEFAULT_ERROR_RATE_LIMIT_S = 60.0


def _build_pool() -> Optional[pg_pool.ThreadedConnectionPool]:
    """Initialize the pool on first call. Returns None if DATABASE_URL
    is unset or the initial connect fails."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            log.warning("strategy_db_pool_no_url DATABASE_URL not set")
            return None
        try:
            _pool = pg_pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=8,
                dsn=url,
                connect_timeout=5,
            )
            log.info("strategy_db_pool_initialized minconn=1 maxconn=8")
        except Exception as e:  # noqa: BLE001
            log.warning("strategy_db_pool_init_failed error=%s", e)
            return None
    return _pool


@contextmanager
def get_conn() -> Iterator[Optional[psycopg2.extensions.connection]]:
    """Acquire a connection from the pool and return it on exit.

    Yields None if the pool can't be built (no DATABASE_URL or initial
    connect failed). Callers must check for None.

    On exception inside the `with` block, the connection is rolled
    back before being returned to the pool, so a partially-executed
    transaction never leaks state into the next caller.
    """
    pool = _build_pool()
    if pool is None:
        yield None
        return
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn)
            except Exception as e:  # noqa: BLE001
                log.warning("strategy_db_pool_putconn_failed error=%s", e)


def report_db_error(
    operation: str,
    error: BaseException,
    *,
    rate_limit_s: float = DEFAULT_ERROR_RATE_LIMIT_S,
) -> None:
    """Emit a rate-limited WARNING for a DB exception.

    Suppresses repeated warnings for the same `operation` within
    `rate_limit_s` seconds so a sustained outage produces a steady
    drip of one log per minute rather than thousands per second.
    """
    now = time.monotonic()
    with _error_state_lock:
        last = _error_last_log_ts.get(operation, 0.0)
        if now - last < rate_limit_s:
            return
        _error_last_log_ts[operation] = now
    log.warning(
        "strategy_db_error operation=%s error_type=%s error=%s",
        operation, type(error).__name__, error,
    )


def close_pool() -> None:
    """Close all connections. Intended for process shutdown only."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:  # noqa: BLE001
                pass
            _pool = None
