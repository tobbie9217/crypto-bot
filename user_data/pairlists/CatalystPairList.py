"""CatalystPairList — top-N-by-volume plus catalyst-driven extras.

A custom freqtrade pairlist that augments the standard volume-based
universe with coins that have an active catalyst in Postgres:

  1. New Binance listings within the last `new_listing_hours`.
     (Note: downstream AgeFilter still requires min_days_listed=1,
      and RangeStabilityFilter needs 3 days of price history, so
      very-fresh listings won't survive the full filter chain.)

  2. Sentiment z-score >= `sentiment_z_min` in `sentiment_aggregates`
     within the last `sentiment_lookback_hours`.

  3. Funding-rate or open-interest z-score >= `onchain_z_min` in
     `onchain_aggregates` within `onchain_lookback_hours`.

The catalyst extras are appended to the volume baseline (deduped,
capped at `max_extras`) so we never *shrink* the universe — only
augment it with coins likely to move in the next few hours.

DB access is per-refresh (default every 30 min), not per-candle,
so a single short psycopg2 connection per gen_pairlist call is fine.
"""
import logging
import os
from datetime import timedelta

import psycopg2

from freqtrade.exchange.exchange_types import Tickers
from freqtrade.plugins.pairlist.IPairList import IPairList

logger = logging.getLogger(__name__)


class CatalystPairList(IPairList):

    is_pairlist_generator = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Volume baseline (same shape as VolumePairList)
        self._stake_currency: str = self._config["stake_currency"]
        self._number_assets: int = self._pairlistconfig.get("number_assets", 50)
        self._min_value: float = self._pairlistconfig.get("min_value", 0)
        self._refresh_period: int = self._pairlistconfig.get("refresh_period", 1800)

        # Catalyst thresholds
        self._new_listing_hours: int = self._pairlistconfig.get(
            "new_listing_hours", 72)
        self._sentiment_lookback_hours: int = self._pairlistconfig.get(
            "sentiment_lookback_hours", 6)
        self._sentiment_z_min: float = self._pairlistconfig.get(
            "sentiment_z_min", 1.5)
        self._onchain_lookback_hours: int = self._pairlistconfig.get(
            "onchain_lookback_hours", 6)
        self._onchain_z_min: float = self._pairlistconfig.get(
            "onchain_z_min", 3.0)
        self._max_extras: int = self._pairlistconfig.get("max_extras", 20)

    @property
    def needstickers(self) -> bool:
        return True

    def short_desc(self) -> str:
        return (
            f"{self.name} - top {self._number_assets} by volume "
            f"+ up to {self._max_extras} catalyst pairs"
        )

    @staticmethod
    def description() -> str:
        return (
            "Top-N-by-volume baseline augmented with coins flagged by "
            "active catalysts (new Binance listings, sentiment z-spikes, "
            "funding/open-interest z-spikes) queried from Postgres."
        )

    # -------------------- pairlist generation --------------------

    def gen_pairlist(self, tickers: Tickers) -> list[str]:
        baseline = self._volume_baseline(tickers)
        extras = self._catalyst_extras(tickers, exclude=set(baseline))
        if extras:
            logger.info(
                "catalyst_pairs_added count=%d pairs=%s",
                len(extras), ",".join(extras),
            )
        return baseline + extras

    # -------------------- volume baseline --------------------

    def _volume_baseline(self, tickers: Tickers) -> list[str]:
        """Top N tradable USDT pairs by quoteVolume, mirroring
        VolumePairList behaviour."""
        candidates: list[tuple[str, float]] = []
        suffix = f"/{self._stake_currency}"
        for pair, tk in tickers.items():
            if not pair.endswith(suffix):
                continue
            qv = tk.get("quoteVolume") or 0
            if qv < self._min_value:
                continue
            candidates.append((pair, float(qv)))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in candidates[: self._number_assets]]

    # -------------------- catalyst extras --------------------

    def _catalyst_extras(
        self, tickers: Tickers, exclude: set[str]
    ) -> list[str]:
        coins = self._discover_catalyst_coins()
        out: list[str] = []
        suffix = f"/{self._stake_currency}"
        for coin in coins:
            if coin == "MARKET":  # sentinel for market-wide F&G aggregates
                continue
            pair = f"{coin}{suffix}"
            if pair in exclude or pair in out:
                continue
            tk = tickers.get(pair)
            if tk is None:
                continue
            qv = tk.get("quoteVolume") or 0
            if qv < self._min_value:
                continue
            out.append(pair)
            if len(out) >= self._max_extras:
                break
        return out

    def _discover_catalyst_coins(self) -> list[str]:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            logger.warning("catalyst_query_skipped reason=no_DATABASE_URL")
            return []
        coins: set[str] = set()
        try:
            with psycopg2.connect(db_url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    coins.update(self._fetch_new_listings(cur))
                    coins.update(self._fetch_sentiment_spikes(cur))
                    coins.update(self._fetch_onchain_spikes(cur))
        except Exception as e:  # noqa: BLE001
            logger.warning("catalyst_query_failed error=%s", e)
            return []
        return sorted(coins)

    def _fetch_new_listings(self, cur) -> list[str]:
        cur.execute(
            """
            SELECT DISTINCT payload->>'symbol' AS sym
            FROM bot_events
            WHERE event_type = 'binance_new_listing'
              AND created_at >= NOW() - %s
            """,
            (timedelta(hours=self._new_listing_hours),),
        )
        out: list[str] = []
        suffix = self._stake_currency
        for (sym,) in cur.fetchall():
            if sym and sym.endswith(suffix):
                out.append(sym[: -len(suffix)])
        return out

    def _fetch_sentiment_spikes(self, cur) -> list[str]:
        cur.execute(
            """
            SELECT DISTINCT coin
            FROM sentiment_aggregates
            WHERE time_window = '1h'
              AND bucket_start >= NOW() - %s
              AND z_score >= %s
            """,
            (timedelta(hours=self._sentiment_lookback_hours),
             self._sentiment_z_min),
        )
        return [c for (c,) in cur.fetchall() if c]

    def _fetch_onchain_spikes(self, cur) -> list[str]:
        cur.execute(
            """
            SELECT DISTINCT coin
            FROM onchain_aggregates
            WHERE time_window = '1h'
              AND metric IN ('funding_rate', 'open_interest')
              AND bucket_start >= NOW() - %s
              AND z_score >= %s
            """,
            (timedelta(hours=self._onchain_lookback_hours),
             self._onchain_z_min),
        )
        return [c for (c,) in cur.fetchall() if c]
