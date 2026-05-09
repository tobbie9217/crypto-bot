"""
SentimentStrategy — combines CryptoBERT sentiment with classic technicals.

Reads the most recent 1h aggregate from `sentiment_aggregates` (written by
the sentiment service) and uses it as an entry/exit gate alongside RSI + EMA.

Entry conditions (ALL must be true):
- sentiment z-score > 1.5         (unusual positive social swing)
- mean sentiment > 0.2            (genuinely bullish, not just chatter)
- post_count >= 5                 (enough volume to trust)
- RSI < 70                        (not already overbought)
- EMA(12) > EMA(50)               (price is in an uptrend)

Exit conditions (ANY triggers):
- mean sentiment < -0.1           (sentiment flipped)
- RSI > 80                        (extreme overbought)
"""
import os
from typing import Optional

import psycopg2
import psycopg2.extras
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class SentimentStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"

    minimal_roi = {
        "120": 0.01,
        "60": 0.02,
        "30": 0.03,
        "0": 0.05,
    }

    stoploss = -0.03

    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count: int = 50

    def _db_url(self) -> str:
        return os.environ.get("DATABASE_URL", "")

    def _latest_sentiment(self, coin: str) -> tuple[float, float, int]:
        """Return (mean, z_score, count) for the most recent 1h bucket, or zeros."""
        url = self._db_url()
        if not url:
            return (0.0, 0.0, 0)
        try:
            with psycopg2.connect(url, connect_timeout=5) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(
                        """
                        SELECT mean_score, z_score, post_count
                        FROM sentiment_aggregates
                        WHERE coin = %s AND time_window = '1h'
                          AND bucket_start >= NOW() - INTERVAL '2 hours'
                        ORDER BY bucket_start DESC
                        LIMIT 1
                        """,
                        (coin,),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        return (
                            float(row["mean_score"]),
                            float(row["z_score"] or 0.0),
                            int(row["post_count"]),
                        )
        except Exception:
            # Strategy must never crash from a DB hiccup. Falling back to
            # neutral zeros effectively disables sentiment for this candle.
            pass
        return (0.0, 0.0, 0)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=50)

        # Sentiment is a single live scalar per pair; we tile it across all
        # rows of the dataframe. Do NOT use this for backtests — backtest
        # would need historical aggregates joined by candle timestamp.
        coin = metadata["pair"].split("/")[0]
        mean, z, count = self._latest_sentiment(coin)
        dataframe["sentiment_mean"] = mean
        dataframe["sentiment_z"] = z
        dataframe["sentiment_count"] = count

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["sentiment_z"] > 1.5)
                & (dataframe["sentiment_mean"] > 0.2)
                & (dataframe["sentiment_count"] >= 5)
                & (dataframe["rsi"] < 70)
                & (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (
                    (dataframe["sentiment_mean"] < -0.1)
                    | (dataframe["rsi"] > 80)
                )
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1
        return dataframe
