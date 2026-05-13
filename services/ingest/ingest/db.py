import json
from datetime import datetime
from typing import Any

import asyncpg


class DB:
    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def insert_post(
        self,
        *,
        source: str,
        source_id: str,
        text: str,
        posted_at: datetime,
        coin: str | None = None,
        author: str | None = None,
        url: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> int | None:
        """Insert a post, idempotent on (source, source_id).

        Returns the new id on insert, or None if a row already existed.
        """
        assert self.pool is not None, "DB.connect() not called"
        raw_json = json.dumps(raw, default=str) if raw is not None else None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO posts (source, source_id, coin, text, author, url, posted_at, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (source, source_id) DO NOTHING
                RETURNING id
                """,
                source, source_id, coin, text, author, url, posted_at, raw_json,
            )
        return row["id"] if row else None

    async def insert_onchain_metric(
        self,
        *,
        coin: str,
        metric: str,
        value: float,
        source: str,
        observed_at: datetime,
        raw: dict[str, Any] | None = None,
    ) -> int | None:
        """Insert a single on-chain / market metric observation.

        Idempotent on (coin, metric, source, observed_at) — re-running a
        collector with overlapping timestamps is safe.
        """
        assert self.pool is not None, "DB.connect() not called"
        raw_json = json.dumps(raw, default=str) if raw is not None else None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO onchain_metrics (coin, metric, value, source, observed_at, raw)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (coin, metric, source, observed_at) DO NOTHING
                RETURNING id
                """,
                coin, metric, value, source, observed_at, raw_json,
            )
        return row["id"] if row else None

    async def insert_ohlcv_rows(
        self,
        rows: list[tuple[
            str,        # exchange
            str,        # symbol
            str,        # coin
            str,        # timeframe
            datetime,   # ts
            float,      # open
            float,      # high
            float,      # low
            float,      # close
            float,      # volume
            float | None,  # quote_volume
            int | None,    # trades
            float | None,  # taker_buy_base_volume
            float | None,  # taker_buy_quote_volume
        ]],
    ) -> int:
        """Bulk-insert OHLCV candles. ON CONFLICT updates so an in-progress
        candle re-inserted on a later cycle overwrites the previous draft.

        Returns the number of rows touched (inserted or updated).
        """
        assert self.pool is not None, "DB.connect() not called"
        if not rows:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO ohlcv (
                    exchange, symbol, coin, timeframe, ts,
                    open, high, low, close, volume,
                    quote_volume, trades,
                    taker_buy_base_volume, taker_buy_quote_volume
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT (exchange, symbol, timeframe, ts) DO UPDATE SET
                    open                   = EXCLUDED.open,
                    high                   = EXCLUDED.high,
                    low                    = EXCLUDED.low,
                    close                  = EXCLUDED.close,
                    volume                 = EXCLUDED.volume,
                    quote_volume           = EXCLUDED.quote_volume,
                    trades                 = EXCLUDED.trades,
                    taker_buy_base_volume  = EXCLUDED.taker_buy_base_volume,
                    taker_buy_quote_volume = EXCLUDED.taker_buy_quote_volume,
                    updated_at             = NOW()
                """,
                rows,
            )
        return len(rows)
