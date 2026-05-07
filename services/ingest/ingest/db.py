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
