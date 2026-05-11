from pandas import DataFrame
from freqtrade.strategy import IStrategy
import talib.abstract as ta


class SampleStrategy(IStrategy):
    """
    Week-1 placeholder: simple RSI mean-reversion on 5m candles.
    Replace with sentiment-aware strategy in week 4.
    """

    INTERFACE_VERSION = 3

    timeframe = "5m"

    minimal_roi = {
        "60": 0.01,
        "30": 0.02,
        "0": 0.04
    }

    stoploss = -0.05

    trailing_stop = False

    process_only_new_candles = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count: int = 30

    protections = [
        {
            "method": "CooldownPeriod",
            "stop_duration_candles": 6
        },
        {
            "method": "MaxDrawdown",
            "lookback_period_candles": 288,
            "trade_limit": 20,
            "stop_duration_candles": 48,
            "max_allowed_drawdown": 0.05
        },
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 288,
            "trade_limit": 4,
            "stop_duration_candles": 12,
            "only_per_pair": False
        },
        {
            "method": "LowProfitPairs",
            "lookback_period_candles": 1440,
            "trade_limit": 4,
            "stop_duration_candles": 60,
            "required_profit": 0.01
        }
    ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=26)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["rsi"] < 30)
                & (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["rsi"] > 70)
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1
        return dataframe
