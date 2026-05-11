"""
SentimentOnchainStrategy — three-layer entry filter.

Combines text sentiment with on-chain / derivatives signals from the
ingest pipeline:

  Layer 1  text sentiment       — sentiment_aggregates  (CryptoBERT)
  Layer 2  derivatives          — onchain_aggregates     (funding, open
                                  interest, mark price from Binance)
  Layer 3  on-chain & macro     — onchain_aggregates     (chain TVL,
                                  market-wide Fear & Greed)

Entry is intentionally rare: ALL three layers must agree, plus classic
technicals (RSI, EMA cross). The cost is fewer trades; the benefit is
each entry has multiple independent confirmations.

NOT BACKTESTABLE without backfilled aggregates — the strategy queries
live tables that don't have history for a backtest range. Run dry-run
only until backfill is implemented.

Warmup: z-scores need ~7 days of bucket history to stabilize. Until then
the strategy will mostly sit idle. That's by design — better to miss a
few setups than to enter on noise.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, merge_informative_pair
from pandas import DataFrame


class SentimentOnchainStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    # Higher timeframe used as a trend gate. Filters out 5m noise that
    # cuts against the 1h trend.
    informative_timeframe = "1h"

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

    # 288 = 24h of 5-min candles, needed for the 24h volume-MA used by the
    # momentum entry path.
    startup_candle_count: int = 300

    # Per-tag risk routing: the strict path uses the class-level stoploss
    # (-3%) and trailing config; the momentum path overrides via
    # custom_stoploss / custom_stake_amount below.
    use_custom_stoploss = True

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

    # --- Tunable thresholds (named so they're easy to grep & adjust) ---

    # Text sentiment
    SENTIMENT_Z_MIN = 1.0          # was 1.5 in single-signal strategy; lower
                                    # here because we have additional confirmation
    SENTIMENT_MEAN_MIN = 0.15
    SENTIMENT_COUNT_MIN = 5

    # Derivatives — funding rate is in raw decimal form (0.0001 = 1 bp / 8h)
    FUNDING_MAX_ENTER = 0.0005      # 5 bps / 8h — block entry if funding overheated
    FUNDING_MAX_EXIT  = 0.001       # 10 bps / 8h — exit if funding spikes

    OI_Z_MIN = 0.0                  # rising open interest confirms participation

    # On-chain
    TVL_Z_MIN = 0.0                 # rising chain TVL confirms ecosystem demand
                                    # (gate skipped for non-chain tokens like PEPE)

    # Market-wide
    FNG_MAX_ENTER = 75              # don't buy into "Extreme Greed"
    FNG_MAX_EXIT  = 85              # exit on "Extreme Greed"

    # Exits
    SENTIMENT_FLIP_NEG = -0.1
    RSI_OVERBOUGHT = 80

    # --- Momentum entry path (catches DOGS-style fast pumps) ---
    # Pure price-action entries that don't require sentiment baseline /
    # z-scores. Tagged "momentum" so risk hooks below can apply tighter
    # stops and smaller size.
    MOMENTUM_PRICE_CHANGE_1H = 8.0      # +8% in last hour
    MOMENTUM_VOLUME_MULTIPLIER = 3.0    # ≥3× the 24h average volume
    MOMENTUM_RSI_MAX = 75               # don't chase if already overbought
    MOMENTUM_CHATTER_COUNT = 30         # alt path: post volume spike

    # Risk overrides for momentum-tagged trades
    MOMENTUM_STAKE_FRACTION = 0.30      # 30% of normal position size
    MOMENTUM_TRAILING_STOP = 0.01       # 1% trailing once in profit
    MOMENTUM_HARD_STOP = -0.03          # 3% hard stop

    # --- Soft-veto thresholds for momentum entries ---
    # Each check fires only when the signal data is *available*. Missing
    # data = signal skipped = trade allowed. The veto can only block,
    # never require — so coins with sparse data still get the momentum
    # path, just without the extra filter.
    MOMENTUM_VETO_FUNDING_MAX = 0.001        # block if funding > 10 bps/8h
    MOMENTUM_VETO_SENTIMENT_MIN = 0.0        # block if avg sentiment < 0
    MOMENTUM_VETO_SENTIMENT_COUNT_MIN = 5    # ...and we have 5+ posts
    MOMENTUM_VETO_TOP_TRADER_LSR_MIN = 0.7   # block if top traders heavily short

    # ----------------------- Higher-timeframe wiring -----------------------

    def informative_pairs(self):
        # Provide 1h candles for every pair in the active whitelist so we
        # can gate entries on the higher-timeframe trend.
        pairs = self.dp.current_whitelist()
        return [(p, self.informative_timeframe) for p in pairs]

    # ----------------------- DB helpers -----------------------

    def _db_url(self) -> str:
        return os.environ.get("DATABASE_URL", "")

    def _latest_sentiment(self, coin: str) -> tuple[float, float, int]:
        """Return (mean, z, count) for the last 1h sentiment bucket."""
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
                        ORDER BY bucket_start DESC LIMIT 1
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
            pass
        return (0.0, 0.0, 0)

    def _latest_onchain(
        self, coin: str, metric: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Return (value, z_score) for the latest 1h on-chain bucket, or (None, None)."""
        url = self._db_url()
        if not url:
            return (None, None)
        try:
            with psycopg2.connect(url, connect_timeout=5) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(
                        """
                        SELECT value, z_score
                        FROM onchain_aggregates
                        WHERE coin = %s AND metric = %s AND time_window = '1h'
                          AND bucket_start >= NOW() - INTERVAL '6 hours'
                        ORDER BY bucket_start DESC LIMIT 1
                        """,
                        (coin, metric),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        return (
                            float(row["value"]) if row["value"] is not None else None,
                            float(row["z_score"]) if row["z_score"] is not None else None,
                        )
        except Exception:
            pass
        return (None, None)

    # ----------------------- Strategy hooks -----------------------

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=50)

        # --- 1h informative timeframe (trend gate) ---
        # Forward-filled into each 5m row so we can reference the most-
        # recent completed 1h state on every candle.
        informative = self.dp.get_pair_dataframe(
            pair=metadata["pair"], timeframe=self.informative_timeframe
        )
        if not informative.empty:
            informative["rsi"] = ta.RSI(informative, timeperiod=14)
            informative["ema_fast"] = ta.EMA(informative, timeperiod=12)
            informative["ema_slow"] = ta.EMA(informative, timeperiod=50)
            dataframe = merge_informative_pair(
                dataframe, informative,
                self.timeframe, self.informative_timeframe,
                ffill=True,
            )
        else:
            # No 1h data yet (new pair) — neutral defaults so entry rules
            # don't fire on garbage.
            dataframe["rsi_1h"] = 50.0
            dataframe["ema_fast_1h"] = 0.0
            dataframe["ema_slow_1h"] = 0.0

        # --- Momentum features (no DB dependency, pure price action) ---
        # 12 candles × 5m = 1 hour
        dataframe["price_change_1h"] = dataframe["close"].pct_change(periods=12) * 100.0
        # 288 candles × 5m = 24 hours
        dataframe["volume_ma_24h"] = dataframe["volume"].rolling(window=288, min_periods=24).mean()
        dataframe["volume_ratio"] = (
            dataframe["volume"] / dataframe["volume_ma_24h"]
        ).fillna(1.0)

        coin = metadata["pair"].split("/")[0]

        # --- Layer 1: text sentiment ---
        s_mean, s_z, s_count = self._latest_sentiment(coin)
        dataframe["sentiment_mean"]  = s_mean
        dataframe["sentiment_z"]     = s_z
        dataframe["sentiment_count"] = s_count

        # --- Layer 2: derivatives ---
        funding_v, _ = self._latest_onchain(coin, "funding_rate")
        # Treat missing funding as "neutral 0" so the funding gate doesn't
        # block entries for coins without perp data.
        dataframe["funding_rate"] = funding_v if funding_v is not None else 0.0

        _, oi_z = self._latest_onchain(coin, "open_interest")
        dataframe["oi_z"] = oi_z if oi_z is not None else 0.0

        # --- Layer 3: on-chain & macro ---
        _, tvl_z = self._latest_onchain(coin, "chain_tvl")
        # has_tvl_data lets us OR-around the TVL gate for tokens without
        # a chain (e.g. PEPE, AAVE, RUNE). For chain natives (ETH, SOL,
        # BNB...) the z-score must be > threshold.
        dataframe["tvl_z"]         = tvl_z if tvl_z is not None else 0.0
        dataframe["has_tvl_data"]  = 1 if tvl_z is not None else 0

        fng_value, _ = self._latest_onchain("MARKET", "fear_greed_index")
        # Default to 50 (neutral) when missing so the F&G gate doesn't
        # accidentally block entries on data-collection hiccups.
        dataframe["fng"] = fng_value if fng_value is not None else 50.0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1h trend gate — used by BOTH paths. We require the higher-tf to
        # at least be in an uptrend before considering any 5m entry.
        htf_uptrend = dataframe["ema_fast_1h"] > dataframe["ema_slow_1h"]
        htf_not_overbought = dataframe["rsi_1h"] < 75

        # ----- Strict path: high-conviction, multi-signal confluence -----
        strict = (
            (dataframe["sentiment_z"] > self.SENTIMENT_Z_MIN)
            & (dataframe["sentiment_mean"] > self.SENTIMENT_MEAN_MIN)
            & (dataframe["sentiment_count"] >= self.SENTIMENT_COUNT_MIN)
            & (dataframe["funding_rate"] < self.FUNDING_MAX_ENTER)
            & (dataframe["oi_z"] > self.OI_Z_MIN)
            & (
                (dataframe["tvl_z"] > self.TVL_Z_MIN)
                | (dataframe["has_tvl_data"] == 0)
            )
            & (dataframe["fng"] < self.FNG_MAX_ENTER)
            & (dataframe["rsi"] < 70)
            & (dataframe["ema_fast"] > dataframe["ema_slow"])
            & htf_uptrend
            & htf_not_overbought
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[strict, ["enter_long", "enter_tag"]] = (1, "strict")

        # ----- Momentum path: catch fast pumps without sentiment baseline -----
        # Either a price+volume eruption OR a chatter-volume spike.
        # 1h must at least be in an uptrend; we skip the rsi_1h gate so we
        # can still enter when 1h RSI is hot during a real pump.
        momentum_price = (
            (dataframe["price_change_1h"] > self.MOMENTUM_PRICE_CHANGE_1H)
            & (dataframe["volume_ratio"] > self.MOMENTUM_VOLUME_MULTIPLIER)
            & (dataframe["rsi"] < self.MOMENTUM_RSI_MAX)
            & htf_uptrend
            & (dataframe["volume"] > 0)
        )
        momentum_chatter = (
            (dataframe["sentiment_count"] > self.MOMENTUM_CHATTER_COUNT)
            & (dataframe["volume_ratio"] > 2.0)
            & (dataframe["rsi"] < self.MOMENTUM_RSI_MAX)
            & htf_uptrend
            & (dataframe["volume"] > 0)
        )
        # Don't double-tag rows already flagged by the strict path.
        momentum = (momentum_price | momentum_chatter) & (~strict)
        dataframe.loc[momentum, ["enter_long", "enter_tag"]] = (1, "momentum")

        # Log every newly-fired entry attempt so we can later see what
        # Freqtrade's protections / max_open_trades / slippage rejected
        # vs what got through to confirm_trade_entry.
        self._log_attempted_entry_if_new(dataframe, metadata.get("pair"))

        return dataframe

    def _log_attempted_entry_if_new(
        self, dataframe: DataFrame, pair: Optional[str]
    ) -> None:
        """Log an 'attempted_entry' row only on the candle where
        enter_long transitions 0 -> 1 (deduped against persistent flags)."""
        if pair is None or len(dataframe) < 2:
            return
        last = dataframe.iloc[-1]
        prev = dataframe.iloc[-2]
        try:
            last_flag = int(last.get("enter_long") or 0)
            prev_flag = int(prev.get("enter_long") or 0)
        except (TypeError, ValueError):
            return
        if last_flag != 1 or prev_flag == 1:
            return
        tag = last.get("enter_tag")
        if hasattr(tag, "item"):
            tag = tag.item()
        try:
            close = float(last["close"])
        except (TypeError, ValueError, KeyError):
            close = 0.0
        self._log_trade_event(
            pair=pair,
            event="attempted_entry",
            rate=close,
            enter_tag=str(tag) if tag else None,
            thresholds=self._current_thresholds(),
        )

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit conditions apply to BOTH paths. Momentum trades typically
        # exit via the trailing stop in custom_stoploss before reaching
        # these — that's by design.
        dataframe.loc[
            (
                (
                    (dataframe["sentiment_mean"] < self.SENTIMENT_FLIP_NEG)
                    | (dataframe["funding_rate"] > self.FUNDING_MAX_EXIT)
                    | (dataframe["fng"] > self.FNG_MAX_EXIT)
                    | (dataframe["rsi"] > self.RSI_OVERBOUGHT)
                )
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1
        return dataframe

    # ----------------------- Per-tag risk routing -----------------------

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        # Momentum entries are inherently riskier — size them at a fraction
        # of the strict-path stake.
        if entry_tag == "momentum":
            return max(min_stake or 0, proposed_stake * self.MOMENTUM_STAKE_FRACTION)
        return proposed_stake

    def custom_stoploss(
        self,
        pair: str,
        trade: "Trade",
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> Optional[float]:
        # Strict trades use the class-level stoploss + trailing config.
        if trade.enter_tag != "momentum":
            return None
        # Momentum trades: tighter trailing once in profit, hard 3% stop
        # otherwise. Returning a positive number relative to current price
        # would be wrong; we return a negative ratio off entry.
        if current_profit > 0.02:
            # Trail at ~1% behind highest profit. Approximate by setting
            # the stop at current_profit - 0.01 (so each new high tightens
            # the stop).
            return current_profit - self.MOMENTUM_TRAILING_STOP
        return self.MOMENTUM_HARD_STOP

    # ----------------------- Trade journal -----------------------

    # Columns we snapshot from the most recent candle's indicators. Stored
    # in trade_journal.features as JSONB so we can query them later.
    _SNAPSHOT_COLUMNS: tuple[str, ...] = (
        "close", "rsi", "ema_fast", "ema_slow",
        "rsi_1h", "ema_fast_1h", "ema_slow_1h",
        "sentiment_mean", "sentiment_z", "sentiment_count",
        "funding_rate", "oi_z", "tvl_z", "has_tvl_data", "fng",
        "price_change_1h", "volume_ratio",
    )

    def _snapshot_features(self, pair: str) -> dict:
        """Pull the latest analyzed-candle features for `pair`,
        plus market-wide context (BTC state, F&G — already in row but
        repeated under btc_* prefix for explicit cross-coin context)."""
        out: dict = {}
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                for col in self._SNAPSHOT_COLUMNS:
                    if col in df.columns:
                        val = last[col]
                        if hasattr(val, "item"):
                            val = val.item()
                        out[col] = val
        except Exception:
            pass

        # Market-wide context — useful when slicing by regime later.
        # Cheap: two DB lookups per trade event, fires only at entry/exit.
        try:
            btc_mean, btc_z, btc_count = self._latest_sentiment("BTC")
            out["btc_sentiment_mean"] = btc_mean
            out["btc_sentiment_z"]    = btc_z
            out["btc_sentiment_count"] = btc_count
        except Exception:
            pass
        try:
            btc_price, _ = self._latest_onchain("BTC", "price_usd")
            if btc_price is not None:
                out["btc_price_usd"] = btc_price
        except Exception:
            pass
        return out

    def _momentum_veto_reason(self, pair: str) -> Optional[str]:
        """Return a reason string if a momentum entry should be vetoed,
        or None to let it proceed. Each check fires only when the signal
        data is available — soft veto, never blocks for missing data.
        """
        coin = pair.split("/")[0]

        # 1. Funding rate — overheated longs are pump-and-dump territory
        funding, _ = self._latest_onchain(coin, "funding_rate")
        if funding is not None and funding > self.MOMENTUM_VETO_FUNDING_MAX:
            return f"funding_overheated:{funding:.5f}"

        # 2. Sentiment polarity (only trust if we have enough posts)
        s_mean, _s_z, s_count = self._latest_sentiment(coin)
        if (
            s_count >= self.MOMENTUM_VETO_SENTIMENT_COUNT_MIN
            and s_mean < self.MOMENTUM_VETO_SENTIMENT_MIN
        ):
            return f"sentiment_bearish:{s_mean:.3f}"

        # 3. Smart-money positioning — top-trader L/S ratio < 0.7
        # means top traders are net-short on this perp.
        lsr, _ = self._latest_onchain(coin, "top_trader_lsr")
        if lsr is not None and lsr < self.MOMENTUM_VETO_TOP_TRADER_LSR_MIN:
            return f"smart_money_short:{lsr:.2f}"

        return None

    def _current_thresholds(self) -> dict:
        """Snapshot of every entry/exit threshold we tune. Lets future
        audits attribute trades to the threshold values that were live."""
        return {
            "SENTIMENT_Z_MIN":           self.SENTIMENT_Z_MIN,
            "SENTIMENT_MEAN_MIN":        self.SENTIMENT_MEAN_MIN,
            "SENTIMENT_COUNT_MIN":       self.SENTIMENT_COUNT_MIN,
            "FUNDING_MAX_ENTER":         self.FUNDING_MAX_ENTER,
            "FUNDING_MAX_EXIT":          self.FUNDING_MAX_EXIT,
            "OI_Z_MIN":                  self.OI_Z_MIN,
            "TVL_Z_MIN":                 self.TVL_Z_MIN,
            "FNG_MAX_ENTER":             self.FNG_MAX_ENTER,
            "FNG_MAX_EXIT":              self.FNG_MAX_EXIT,
            "SENTIMENT_FLIP_NEG":        self.SENTIMENT_FLIP_NEG,
            "RSI_OVERBOUGHT":            self.RSI_OVERBOUGHT,
            "MOMENTUM_PRICE_CHANGE_1H":  self.MOMENTUM_PRICE_CHANGE_1H,
            "MOMENTUM_VOLUME_MULTIPLIER":self.MOMENTUM_VOLUME_MULTIPLIER,
            "MOMENTUM_RSI_MAX":          self.MOMENTUM_RSI_MAX,
            "MOMENTUM_CHATTER_COUNT":    self.MOMENTUM_CHATTER_COUNT,
            "MOMENTUM_STAKE_FRACTION":   self.MOMENTUM_STAKE_FRACTION,
            "MOMENTUM_TRAILING_STOP":    self.MOMENTUM_TRAILING_STOP,
            "MOMENTUM_HARD_STOP":        self.MOMENTUM_HARD_STOP,
            "MOMENTUM_VETO_FUNDING_MAX":           self.MOMENTUM_VETO_FUNDING_MAX,
            "MOMENTUM_VETO_SENTIMENT_MIN":         self.MOMENTUM_VETO_SENTIMENT_MIN,
            "MOMENTUM_VETO_SENTIMENT_COUNT_MIN":   self.MOMENTUM_VETO_SENTIMENT_COUNT_MIN,
            "MOMENTUM_VETO_TOP_TRADER_LSR_MIN":    self.MOMENTUM_VETO_TOP_TRADER_LSR_MIN,
        }

    def _log_trade_event(
        self,
        *,
        pair: str,
        event: str,
        rate: float,
        enter_tag: Optional[str] = None,
        exit_reason: Optional[str] = None,
        profit_ratio: Optional[float] = None,
        profit_abs: Optional[float] = None,
        trade_id: Optional[int] = None,
        duration_seconds: Optional[int] = None,
        max_profit_ratio: Optional[float] = None,
        min_profit_ratio: Optional[float] = None,
        thresholds: Optional[dict] = None,
    ) -> None:
        url = self._db_url()
        if not url:
            return
        coin = pair.split("/")[0]
        features = self._snapshot_features(pair)
        try:
            with psycopg2.connect(url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO trade_journal
                            (trade_id, pair, coin, side, event, enter_tag,
                             exit_reason, rate, profit_ratio, profit_abs,
                             duration_seconds, max_profit_ratio, min_profit_ratio,
                             thresholds, features)
                        VALUES (%s, %s, %s, 'long', %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s::jsonb, %s::jsonb)
                        """,
                        (
                            trade_id, pair, coin, event, enter_tag,
                            exit_reason, rate, profit_ratio, profit_abs,
                            duration_seconds, max_profit_ratio, min_profit_ratio,
                            json.dumps(thresholds, default=str) if thresholds is not None else None,
                            json.dumps(features, default=str),
                        ),
                    )
        except Exception:
            # Journaling must never block a trade. Swallow DB errors.
            pass

        # Best-effort rich notification. Skipped for `attempted_entry`
        # (high volume) and on errors. Never blocks the trade flow.
        if event != "attempted_entry":
            try:
                msg = self._format_telegram_alert(
                    event=event, pair=pair, rate=rate, enter_tag=enter_tag,
                    exit_reason=exit_reason, profit_ratio=profit_ratio,
                    profit_abs=profit_abs, duration_seconds=duration_seconds,
                    max_profit_ratio=max_profit_ratio,
                    min_profit_ratio=min_profit_ratio, features=features,
                )
                self._send_telegram_alert(msg)
            except Exception:
                pass

    # ----------------------- Telegram alerts -----------------------

    def _send_telegram_alert(self, message: str) -> None:
        """POST to Telegram via stdlib urllib so we don't depend on
        anything not already in the freqtrade image. Best-effort: any
        failure is swallowed so alerts never block a trade."""
        token = os.environ.get("FREQTRADE__TELEGRAM__TOKEN")
        chat_id = os.environ.get("FREQTRADE__TELEGRAM__CHAT_ID")
        if not token or not chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    @staticmethod
    def _fmt_pct(x: Optional[float]) -> str:
        if x is None:
            return "—"
        return f"{x*100:+.2f}%"

    @staticmethod
    def _fmt_price(x: float) -> str:
        if x >= 1:
            return f"{x:,.4f}"
        # Tighter precision for sub-dollar tokens (PEPE, SHIB, etc.)
        return f"{x:.8g}"

    @staticmethod
    def _fmt_dur(seconds: Optional[int]) -> str:
        if seconds is None:
            return "—"
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        return f"{seconds // 3600}h{(seconds % 3600) // 60}m"

    def _format_telegram_alert(
        self,
        *,
        event: str,
        pair: str,
        rate: float,
        enter_tag: Optional[str],
        exit_reason: Optional[str],
        profit_ratio: Optional[float],
        profit_abs: Optional[float],
        duration_seconds: Optional[int],
        max_profit_ratio: Optional[float],
        min_profit_ratio: Optional[float],
        features: dict,
    ) -> str:
        emoji = {"entry": "🎯", "exit": "🚪", "vetoed": "🛑"}.get(event, "•")
        lines = [f"{emoji} *{event.upper()}*  `{pair}`  @ `{self._fmt_price(rate)}`"]

        f = features or {}

        def _ff(key: str, fmt: str = ".3g") -> str:
            v = f.get(key)
            if v is None:
                return "—"
            try:
                return format(float(v), fmt)
            except (TypeError, ValueError):
                return "—"

        if event == "entry":
            lines.append(f"Tag: *{enter_tag or 'unknown'}*")
            lines.append(
                f"RSI: {_ff('rsi', '.1f')} (1h: {_ff('rsi_1h', '.1f')})"
            )
            pc1h = f.get("price_change_1h")
            vr = f.get("volume_ratio")
            if pc1h is not None or vr is not None:
                lines.append(
                    f"1h: {_ff('price_change_1h', '+.2f')}%  "
                    f"vol: {_ff('volume_ratio', '.2f')}×"
                )
            sc = f.get("sentiment_count") or 0
            if sc:
                lines.append(
                    f"Sentiment: {_ff('sentiment_mean', '+.3f')} "
                    f"(z={_ff('sentiment_z', '+.2f')}, n={int(sc)})"
                )
            else:
                lines.append("Sentiment: no data for this coin")
            fr = f.get("funding_rate")
            if fr:
                lines.append(f"Funding: {_ff('funding_rate', '.5f')}")
            lsr = f.get("oi_z")
            if lsr is not None and lsr != 0:
                lines.append(f"OI z-score: {_ff('oi_z', '+.2f')}")
            lines.append(
                f"BTC ctx: ${_ff('btc_price_usd', ',.0f')} "
                f"sent_z={_ff('btc_sentiment_z', '+.2f')}  fng={_ff('fng', '.0f')}"
            )

        elif event == "exit":
            pl = self._fmt_pct(profit_ratio)
            pl_abs = f"{profit_abs:+.2f} USDT" if profit_abs is not None else ""
            lines.append(f"Tag: *{enter_tag or 'unknown'}*")
            lines.append(f"P&L: *{pl}*  ({pl_abs})")
            lines.append(f"Held: {self._fmt_dur(duration_seconds)}")
            lines.append(
                f"Peak: {self._fmt_pct(max_profit_ratio)}   "
                f"Trough: {self._fmt_pct(min_profit_ratio)}"
            )
            lines.append(f"Reason: `{exit_reason or 'unknown'}`")

        elif event == "vetoed":
            lines.append(f"Tag: *{enter_tag or 'unknown'}*")
            lines.append(f"Veto: `{exit_reason}`")
            lines.append(f"Would-have-entered @ `{self._fmt_price(rate)}`")
            lines.append(
                f"BTC ctx: ${_ff('btc_price_usd', ',.0f')} "
                f"sent_z={_ff('btc_sentiment_z', '+.2f')}  fng={_ff('fng', '.0f')}"
            )

        return "\n".join(lines)

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        # Soft veto on momentum-tagged entries: if any of the secondary
        # signals (funding, sentiment polarity, smart-money positioning)
        # is *available and unfavorable*, block the trade. Missing data
        # is not a blocker — that's what makes it "soft".
        # Strict-tagged trades already passed all gates at entry-rule time.
        if entry_tag == "momentum":
            veto = self._momentum_veto_reason(pair)
            if veto is not None:
                self._log_trade_event(
                    pair=pair, event="vetoed", rate=rate, enter_tag=entry_tag,
                    exit_reason=veto,                  # reuse column for veto reason
                    thresholds=self._current_thresholds(),
                )
                return False

        self._log_trade_event(
            pair=pair, event="entry", rate=rate, enter_tag=entry_tag,
            thresholds=self._current_thresholds(),
        )
        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade: "Trade",
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        try:
            profit_ratio = trade.calc_profit_ratio(rate)
            profit_abs = trade.calc_profit(rate)
        except Exception:
            profit_ratio = None
            profit_abs = None
        # Intra-trade peak / trough — Freqtrade tracks these on the trade.
        try:
            entry = trade.open_rate
            max_r = getattr(trade, "max_rate", None) or entry
            min_r = getattr(trade, "min_rate", None) or entry
            max_profit_ratio = (max_r - entry) / entry if entry else None
            min_profit_ratio = (min_r - entry) / entry if entry else None
        except Exception:
            max_profit_ratio = None
            min_profit_ratio = None
        try:
            duration_seconds = int(
                (current_time - trade.open_date_utc).total_seconds()
            )
        except Exception:
            duration_seconds = None
        self._log_trade_event(
            pair=pair, event="exit", rate=rate,
            enter_tag=trade.enter_tag, exit_reason=exit_reason,
            profit_ratio=profit_ratio, profit_abs=profit_abs,
            trade_id=trade.id,
            duration_seconds=duration_seconds,
            max_profit_ratio=max_profit_ratio,
            min_profit_ratio=min_profit_ratio,
        )
        return True
