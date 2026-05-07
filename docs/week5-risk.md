# Week 5 — Risk hardening

## What this week adds

Risk lives in two places now: the **strategy** (per-trade rules) and the
**Freqtrade config** (account-wide protections).

### Strategy-level (already in SentimentStrategy)

| Setting | Value | Meaning |
|---------|-------|---------|
| `stoploss` | -3% | Hard exit if a trade drops 3% from entry |
| `trailing_stop` | true | Lock in gains as price runs up |
| `trailing_stop_positive` | 1% | Trail by 1% once profitable |
| `trailing_stop_positive_offset` | 2% | Don't activate trailing until +2% |

### Account-level (added to config.json)

| Protection | Trigger | What happens |
|------------|---------|--------------|
| `CooldownPeriod` | After any trade closes | Pair is unavailable for 6 candles (30 min on 5m timeframe) |
| `MaxDrawdown` | -5% account drawdown over last 288 candles (24h) | All new trades blocked for 4 hours |
| `StoplossGuard` | 4 stops within 24h across any pair | All new trades blocked for 1 hour |
| `LowProfitPairs` | Pair averaged < 1% profit over last 1440 candles (5d) | That pair blocked for 5 hours |

### Account-level position sizing

| Setting | Value | Effect |
|---------|-------|--------|
| `dry_run_wallet` | 10000 | $10K simulated balance (week 6 testnet) |
| `max_open_trades` | 3 | At most 3 positions at once |
| `tradable_balance_ratio` | 0.6 | Only deploy 60% of balance — keep 40% as buffer |
| `stake_amount` | `"unlimited"` | Each trade gets `(0.6 * 10000) / 3 = $2000` |

This translates to roughly **2% account risk per trade** with a 3% stop:
`$2000 * 3% / $10000 = 0.6%` of total account at risk per trade. Comfortably
inside the 1–2% rule from the research doc.

## Manual kill-switches

Send to your Telegram bot at any time:

| Command       | Effect |
|---------------|--------|
| `/stop`       | No new entries; existing positions keep running |
| `/forceexit all` | Close every open trade at market |
| `/start`      | Resume trading after a `/stop` |
| `/reload_config` | Re-read config.json after editing protections |

For a hard kill from the host:
```powershell
docker compose stop freqtrade
```

## Verify protections are loaded

```
/show_config
```

In the reply, scroll to `"protections"` — you should see the four blocks.

## Backtest with protections

Protections only apply in `--enable-protections` mode for backtests:
```powershell
docker compose run --rm freqtrade backtesting `
  --strategy SentimentStrategy `
  --enable-protections `
  --timerange 20260101-20260401
```

Compare profit factor and max drawdown with vs. without protections.
A well-tuned protection set should **reduce max drawdown** (the win) at
the cost of slightly **fewer total trades** (the trade-off).

## What's NOT in this week

- No automatic position-size adjustment based on volatility (Kelly /
  ATR-based sizing). The fixed-fractional setup above is fine for the
  starter; we can add adaptive sizing in week 7 if backtests suggest it.
- No correlation cap (e.g. "don't long 5 L1s at once"). With our 4-coin
  whitelist this isn't binding yet. Becomes important once you add 20+ pairs.
