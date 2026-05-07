# Week 4 — Sentiment-aware strategy

## What this week adds

`user_data/strategies/SentimentStrategy.py` — the first strategy that
actually uses the sentiment pipeline. It runs the same 5m timeframe as
the week-1 placeholder but adds three sentiment gates on top of RSI + EMA:

| Gate              | Threshold | Why |
|-------------------|-----------|-----|
| z-score           | `> 1.5`   | Unusual social swing for that coin (vs its own 7-day baseline) |
| mean score        | `> 0.2`   | Genuinely bullish, not just chatter |
| post count        | `>= 5`    | Enough volume to trust the average |
| RSI               | `< 70`    | Filter out overbought entries |
| EMA(12) > EMA(50) | true      | Price is already trending up |

Exits trigger on **either** sentiment flip (`mean < -0.1`) **or** extreme
overbought (`RSI > 80`), plus the global stop-loss / trailing stop.

## Switch to the new strategy

```powershell
# Edit .env: change STRATEGY=SampleStrategy -> STRATEGY=SentimentStrategy
notepad .env

# Recreate the freqtrade container so it picks up the new env value
docker compose up -d --force-recreate freqtrade
docker compose logs -f freqtrade
```

## Verify it's live

In Telegram: send `/status` and `/show_config` — the latter should
report `strategy: SentimentStrategy`.

You won't see entries fire instantly. They fire when **all** five gates
align AND the candle just closed — usually a few per day per coin.

## Run a backtest

The current `_latest_sentiment()` reads the *live* aggregate, not history.
That's fine for paper-trading and live, but for a proper backtest you need
historical aggregates indexed by candle timestamp. Two options:

### Quick smoke backtest (no historical sentiment)
Just verifies the strategy compiles and runs. Sentiment will be 0 for all
candles -> no entries fire. Useful to confirm Freqtrade is happy with your
indicator definitions.

```powershell
docker compose run --rm freqtrade backtesting `
  --config /freqtrade/user_data/config.json `
  --strategy SentimentStrategy `
  --timerange 20260301-20260401 `
  --download-data
```

### Real backtest (historical sentiment join)
This is where you wire `populate_indicators` to do an asof-join against
`sentiment_aggregates` on candle timestamps. Plan for week 4 day 4-5:

1. Backfill 6 months of historical posts (Reddit + LunarCrush snapshots).
2. Run the sentiment service over the backfill once.
3. Modify `_latest_sentiment` to accept a timestamp arg and pull
   `bucket_start <= candle_time` ordered desc limit 1.
4. Walk-forward: train indicator thresholds on month 1-4, validate on
   month 5, paper-trade month 6.

I'll add a `scripts/backfill.py` placeholder when you're ready for that.

## What to watch for

- **Entries never fire** — usually means your sentiment buckets are empty
  or all your z-scores are below 1.5. Check
  `SELECT coin, max(z_score) FROM sentiment_aggregates GROUP BY coin;`
- **DB connection errors** — the strategy gracefully degrades to "no
  sentiment" rather than crashing. Watch `docker compose logs freqtrade`
  for psycopg2 errors and confirm `DATABASE_URL` env var is set in the
  container: `docker compose exec freqtrade env | grep DATABASE_URL`.
