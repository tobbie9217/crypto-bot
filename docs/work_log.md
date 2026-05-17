# Work log

Append-only project journal. Maintained by Claude Code at the end of each
non-trivial session and at major milestones within a session. The point
is **cross-session memory**: a fresh Claude session can read this file
and know exactly where the project stands and what's next.

## Conventions

- **Most recent entry at the top.** Don't edit older entries — just add
  new ones above.
- Each entry has: date, headline, what was built (with file paths), what
  was deployed/verified, results (if any), and what's next.
- Code-complete vs deployed-and-verified are tracked separately. If
  something is committed but not yet running in containers, mark it
  ⏳ rather than ✅.

---

## 2026-05-17 — 90-day backtest reveals no edge; pivoting to futures + shorts

### Summary

Ran five back-to-back backtest experiments on the post-Parts 1+2 strategy
across a statistically meaningful 90-day window. All five lost money. The
PF 1.48 result from the 2026-05-13 entry was a small-sample (4-trade)
fluke; over 90 days with 13 trades the strict-threshold variant has
**PF 0.45 and lost $16 on $10k** in a +9.45% bull market — i.e.
underperformed buy-and-hold by ~9.6%.

The pump-then-pullback momentum thesis on 5m alts with our current data
stack does not have demonstrated edge. Continuing to tune individual
parameters is unlikely to change that — three separate intervention
categories (entry thresholds, exit stops, regime gates) all failed to
move the needle. Decision: **stop iterating on this thesis and pivot to
the long-term futures + shorts direction** that's been on the roadmap
since the 2026-05-13 deep-research audit.

No commit made this session — strategy file restored to its pre-session
state. The session's value is the negative finding documented here.

### Data extension

Before the experiments could be meaningful, we extended the OHLCV
history to support a real 90-day window:

| Action | Command | Result |
|---|---|---|
| Trailing-gap fill | `freqtrade download-data --timerange 20260201-20260517` | Filled 2026-05-13 → 2026-05-17 for all 36 pairs |
| Historical prepend | `freqtrade download-data --timerange 20260201-20260329 --prepend` | Extended back to 2026-02-01 for 35/36 pairs (CHIP/USDT only from 2026-04-21 — listing-date limit) |

Coverage now: BTC, TON, PUMP, etc. all have ~30k rows of 5m candles
2026-02-01 → 2026-05-17 (~3.5 months). Sufficient for 90-day backtests.

### The five experiments

All on `user_data/config_backtest.json` with the 36-pair StaticPairList,
timerange `20260216-20260517` (90 days) except where noted.

| # | Change | Trades | Win% | PF | Net (on $10k) | Reading |
|---|---|---|---|---|---|---|
| 1 | Loose thresholds 5/1/7 (30-day window) | 29 | 58.6 | 0.94 | −$2 | More trades, no edge |
| 2 | Strict thresholds 8/2/5 (90-day window) | 13 | 46.2 | **0.45** | **−$16** | **Real verdict: no edge** |
| 3 | Strict + trailing widened 2%/3% | 13 | 46.2 | 0.36 | −$19 | Wider trail = worse fills |
| 4 | Strict + trailing disabled | 13 | 46.2 | similar | −$19 | Trail not the bug |
| 5 | Strict + tighter BTC regime gate | 6 | 33.3 | 0.33 | −$10 | Filtered trades were the better ones |

Same entry count across runs 2-4 confirms entries are deterministic; only
exits change. Across all five, **ROI exits stay flawless (100% win at
~1.2%)** but **trailing-stop exits dominate and lose 22% win-rate at
−1.75% avg**. Trades briefly hit profit, then reverse decisively. The
pullback-entry signal does not predict continuation in this data.

### Decision

The current spot momentum strategy is a dead end. **Pivot to futures +
shorts.** Reasoning:

- 90-day finding shows the long-only momentum thesis loses in a bull
  market. It will be catastrophic in a sideways or down market.
- A short-side path would let us *profit from* the same reversals that
  are currently eating the long trades.
- Futures + shorts has been the stated long-term direction since at
  least 2026-05-13 (Part 4 of `docs/deep_research.pdf`).
- The infrastructure prerequisite from the 2026-05-13 entry — "profit
  factor ≥ 1.4" — is moot now that we know the 1.48 was small-sample
  noise. The real bar is "demonstrate edge on a 90-day sample," which
  neither path has yet cleared.

### Changes committed this session

All strategy-parameter changes during the session were tested and
reverted to their start-of-session values. Inline comments were added
documenting the negative findings so a fresh session won't redo this
work. Specifically:

- `MOMENTUM_PUMP_SIZE_MIN_PCT/PULLBACK_MIN_PCT/PULLBACK_MAX_PCT`:
  loosened 5/1/7 → reverted to 8/2/5 (where it was at the start)
- `trailing_stop_positive` and `_offset`: tested 0.02/0.03 and
  `trailing_stop=False`, restored to 0.01/0.02
- BTC regime gate: tested adding `close > btc_ema_fast` condition,
  reverted to the simple `btc_ema_fast > btc_ema_slow`
- Inline comments at lines 162-169 and 71-76 now record the negative
  findings so a fresh session won't redo this work

### Live dry-run paused

`docker compose stop freqtrade` issued at end of session. Tried setting
`max_open_trades: 0` first but freqtrade interprets that as a fatal
config and the worker shuts down → docker restart policy causes a
crashloop. Cleanest pause: stop the container. `user_data/config.json`
left at `max_open_trades: 3` so a future `docker compose start
freqtrade` works without further config edits — but **do not start it
until the futures + shorts pivot has something tested**. Other
containers (postgres, ingest, sentiment, audit_bot) still running and
collecting data for future use.

### What's still open

- **Reddit API** — application still pending (submitted 2026-05-12)
- **Order-book + liquidation features** — collecting since 2026-05-13,
  only ~4 days of history. Z-score baselines unreliable yet. Wire in
  once 2+ weeks of history is available. Likely more useful for
  short-side signals than for resurrecting the long-only momentum path
- **Hetzner migration** — decided this session: pay €5-8/mo for a CPX31
  to offload Docker from the laptop. Not yet provisioned. Will become
  the dry-run host once the futures pivot has something to test

### Next moves (futures + shorts pivot — fresh task list)

1. **Find or download a bear-market window** in our data. Current
   coverage is 2026-02-01 → 2026-05-17, all net-positive market. Need
   to identify a down-period and download more history for it (e.g.
   late 2025) for the short-side backtest to be honest
2. **Switch `config_backtest.json` to futures mode** —
   `trading_mode: "futures"`, `margin_mode: "isolated"`, plus margin
   and leverage settings. Verify the 36-pair whitelist exists as
   USDT-M perps
3. **Design short entry logic** — symmetric to long ("dump-then-bounce
   fade")? Or a different thesis (high LSR + negative sentiment + EMA
   roll)? Needs a design conversation before implementation
4. **Backtest short-side alone** on the bear window. Goal: PF ≥ 1.2 on
   ≥20 trades — i.e. clear the statistical-confidence bar this
   session showed we'd been ignoring
5. **Combined long+short backtest** on a mixed-regime window
6. **Hetzner provisioning** — spin up CPX31, migrate stack, start
   futures dry-run there

### Project state at end of session

- All ingest collectors running, postgres healthy, audit_bot live
- Strategy file unchanged from start-of-session state
- Live dry-run still active with the failing long-only strategy —
  recommend pausing it or at minimum reducing stake until the pivot
  produces something tested
- OHLCV data extended to 2026-02-01; this is a real asset for future
  backtest work
- Negative finding documented; do not re-test the same thresholds /
  trailing-stop / BTC-regime variants without a new reason
- Reddit API still pending, Hetzner not yet provisioned

---

## 2026-05-17 (cont.) — Short side also fails; 5m thesis structurally broken

This session continued same-day from the morning's no-edge finding. We
implemented the short logic, set up futures-mode backtest, and ran it on
a real bear window. The result invalidated the *entire pump-pullback /
dump-bounce thesis at the 5m timeframe*, not just the long side.

### What got built

| Component | File / change | Status |
|---|---|---|
| Short logic | `SentimentOnchainStrategy.py` — `can_short=True`, SHORT_* thresholds, dump/bounce/lower-high indicators, `short_failed_bounce` entry tag, mirrored short exits, snapshot + thresholds dict updated | Code-complete |
| Format-aware BTC pair | `_btc_pair()` helper — returns `BTC/USDT` in spot, `BTC/USDT:USDT` in futures. Used by `informative_pairs()` and `get_pair_dataframe()` for the regime gate | ✅ |
| Tri-state BTC regime | `btc_regime_known` column added so both long and short paths fail closed when BTC data is missing (was previously fail-open for shorts) | ✅ |
| Futures backtest config | New `user_data/config_backtest_futures.json` — `trading_mode: futures`, `margin_mode: isolated`, `liquidation_buffer: 0.05`, `:USDT` suffix on 29-pair whitelist, `entry/exit_pricing.price_side: "other"` + `use_order_book: true` (futures requires these — spot config rejected with "Ticker pricing not available") | ✅ |
| Bear-window data | OHLCV for 2024-06 to 2024-10 downloaded for both spot (`config_backtest.json` whitelist, 35/36 pairs back to listing date) and futures (`config_backtest_futures.json`, 28 pairs with mark + funding-rate streams) | ✅ |

### Bear-window futures backtest results (2024-06-04 → 2024-09-30)

| Metric | Value |
|---|---|
| Window | ~120 days |
| Market change | **−15.12%** (real bear: BTC down to −27% mid-window, recovered to −6%) |
| Long trades | 0 (BTC regime down → no longs ever fire — expected) |
| **Short trades** | **6** |
| Win rate | 50% (3W/3L) |
| Profit factor | 0.65 |
| Net P&L | −$3.33 / $10k |
| Best trade | SAGA +1.59% in 5 min |
| Worst trade | DOGE −2.97% in 10 min |
| All exits via | `trailing_stop_loss` (same as longs) |

### The critical finding from per-trade analysis

**5 of 6 trades happened on August 5, 2024** — the famous yen-carry-unwind
flash-crash day where BTC dropped ~15% intraday. The 6th was Aug 3. In
the other **~119 days of the window, the strategy produced ZERO entries.**

| # | Pair | Date | Duration | Profit |
|---|---|---|---|---|
| 1 | SAGA | 2024-08-03 13:45 | 5 min | +1.59% |
| 2 | SOL | 2024-08-05 05:50 | 5 min | +1.14% |
| 3 | SUI | 2024-08-05 07:15 | 10 min | −2.60% |
| 4 | DOGE | 2024-08-05 07:15 | 10 min | −2.97% |
| 5 | SEI | 2024-08-05 10:35 | 10 min | −0.76% |
| 6 | TAO | 2024-08-05 11:00 | 5 min | +1.37% |

Wins caught continuation moves down; losses caught V-bottom reversals.
Within a single flash-crash day, "is the next 5-10 minutes continuation
or reversal?" is essentially a coin flip. The strategy doesn't predict
direction during the only event it fires on.

### What this means structurally

The strategy isn't a "failed-bounce-after-dump" strategy. With these
gates, it's a **flash-crash detector that can't predict direction
within the crash**. The intersection of (`dump ≥5% in 1h`, bounce 1.5-4%,
lower-high, descending 3 closes, RSI 40-65, BTC regime down, soft gates)
basically only happens during black-swan events.

Combined with the morning's finding that the long side lacks edge in
a +9.45% bull, and both sides die the same way (trailing-stop exits
on too-tight stops), the conclusion is structural: **the 5m pump/dump
+ pullback/bounce + multi-signal-gate thesis does not produce a
profitable strategy in either direction**, regardless of regime.

### Decision: major pivot

Stop tuning this thesis. Three real directions worth exploring next,
ranked by expected leverage:

1. **Funding-rate cash-and-carry** (deep_research Part 4.4): short the
   perp when funding is extreme positive, hold spot. Market-neutral,
   edge comes from funding payments not price direction. Doesn't
   require predicting reversals. Most aligned with what we know works
   per the research; needs new strategy file but reuses the data
   infrastructure
2. **Switch timeframe to 1h or 4h**: same indicator stack, much less
   noise. The 5m findings might be entirely a signal-to-noise problem.
   Cheap to test before bigger pivots
3. **Reduce pair scope to top-5 majors + use 1h**: BTC/ETH/SOL/BNB/XRP
   have the cleanest data, lowest spread, deepest order books. The 5m
   alt-coin universe may be where most of the noise is

### Live state

- Freqtrade container still stopped (`docker compose stop freqtrade`
  from earlier today). Do not restart until pivot produces something
  testable
- Strategy file now has short logic + can_short=True + futures-aware
  BTC pair. Will load cleanly in either spot or futures backtests
- `user_data/config_backtest_futures.json` is the futures-mode template
  for any future short backtest work
- Bear-window OHLCV (spot + futures) preserved in data dir; no need
  to re-download for any future short experiments on the same window

### Project state at end of session (cumulative for 2026-05-17)

- Both long-only spot and short-only futures backtests done; both lose
- Negative findings documented exhaustively; do not re-test the same
  parameter/timeframe/signal-stack combinations
- Concrete pivot directions identified; next session needs a design
  conversation on which to pursue
- Reddit API still pending (submitted 2026-05-12)
- Hetzner not yet provisioned — defer until pivot has something tested
- All ingest collectors running, postgres healthy, audit_bot live

---

## 2026-05-13 — Part 1 + Part 2 of `docs/deep_research.pdf` shipped, deployed, backtested

### Summary

Closed every "High" and most "Medium-high" priority data + indicator
gaps from the deep-research audit for which a free, no-key source
exists. Deployed to running containers, ran a 30-day backtest on the
original baseline pair set, and observed real improvement across every
key metric.

Commit: `b41a0c2` on `main`, pushed to
`https://github.com/tobbie9217/crypto-bot`.

### What got built

**Data layer (Part 1 of the audit):**

| Component | File(s) | What it does |
|---|---|---|
| Liquidations WebSocket | `services/ingest/ingest/collectors/binance_liquidations.py` | Subscribes to `!forceOrder@arr`; buffers USD-notional per (coin, side); flushes `liquidation_long_usd` / `liquidation_short_usd` every 60s |
| Order-book depth | `services/ingest/ingest/collectors/binance_orderbook.py` | REST snapshots `/fapi/v1/depth` per perp; derives `orderbook_bid_depth_usd`, `orderbook_ask_depth_usd`, `orderbook_imbalance_1pct` within 1% of mid |
| OHLCV storage | `services/ingest/ingest/collectors/binance_ohlcv.py` + `services/ingest/ingest/scripts/backfill_ohlcv.py` + new `ohlcv` table in `db/schema.sql` | Pulls 1m/5m/1h candles per perp every cycle. Backfill script for one-shot history load |
| Stablecoin supply | `services/ingest/ingest/collectors/defillama_stablecoins.py` | Market-wide `stablecoin_supply_usd` + per-issuer `stablecoin_supply` for USDT/USDC/DAI/FDUSD/USDe/... |
| DEX/CEX volume ratio | `services/ingest/ingest/collectors/market_volume_ratio.py` | DefiLlama + CoinGecko free endpoints → `dex_volume_24h_usd`, `total_volume_24h_usd`, `dex_share_pct` |
| Funding rate predictions | extended `services/ingest/ingest/collectors/binance_derivatives.py` | Adds `index_price`, `premium_index_pct`, `funding_rate_predicted` (Binance's full clamp formula) from fields already in the premiumIndex response |
| Bulk insert helper | `insert_ohlcv_rows()` in `services/ingest/ingest/db.py` | `executemany` + ON CONFLICT DO UPDATE for idempotent in-progress candle upsert |
| RSS expansion | `services/ingest/ingest/collectors/news_rss.py` | Added news.bitcoin.com, CryptoSlate, Protos, AMBCrypto (6 → 10 feeds) |
| Dep | `services/ingest/requirements.txt` | `+ websockets>=12` |

**Strategy layer (Part 1 + Part 2 of the audit):**

| Change | Why |
|---|---|
| LSR + taker buy/sell wired as soft entry gates (both strict and momentum paths) | These three signals were collected at 24k rows/day and ignored. New thresholds: `TOP_TRADER_LSR_MIN = 0.7`, `TAKER_BUY_SELL_MIN = 0.85`. Missing-data fallthrough mirrors funding/fng `_available` pattern |
| `smart_money_divergence = top_trader_lsr - global_account_lsr` derived feature | Smart-money-vs-retail divergence signal |
| ATR(14) indicator | Per-row volatility measure |
| ATR-scaled `custom_stoploss` | Replaces fixed −3%: `clamp(atr_pct × 1.5, [1.5%, 8%])`. Tight on BTC, wide on memecoins |
| Class-level `stoploss` widened to `-0.08` | Safety net matching `ATR_STOP_MAX_PCT` for when df lookup fails |
| `minimal_roi` retuned | Backtest showed winners ~23 min / ~1%. New ladder: 5% @ 0min → 1.5% @ 15min → 0.8% @ 30min → 0% @ 120min |
| EMA fast 12 → 21 (both 5m and 1h) | Reduce whipsaw — deep research called 12 too noisy |
| Bollinger Bands (20-period, ±2σ) as features | `bb_upper`, `bb_middle`, `bb_lower`, `bb_pct_b`. Recorded for now, not wired into entry rules — future mean-reversion path candidate |
| Trade journal snapshot extended | Now includes ATR, BB, LSR, taker, divergence fields |
| `_current_thresholds()` bug fix | Was referencing renamed momentum attributes; would have raised on every entry attempt |

### Deployed and verified (2026-05-13)

1. `docker compose build ingest` — new `websockets` dep installed
2. Schema applied — `ohlcv` table created (verified via `\d ohlcv`)
3. `docker compose up -d ingest` — all 14 collectors scheduled cleanly, no tracebacks
4. Liquidations WebSocket: **connected** to `wss://fstream.binance.com/ws/!forceOrder@arr`
5. Order-book collector first cycle: **529 perps × 3 metrics = 1,587 rows in ~5 min**
6. Funding predictions: **529 rows of `funding_rate_predicted`, `premium_index_pct`, `index_price`** within 5 min
7. Stablecoin + DEX/CEX collectors: first snapshots recorded
8. OHLCV collector: in flight on first cycle (~10-15 min expected, then steady)
9. Freqtrade restarted: loaded new `minimal_roi`, `stoploss=-0.08`, `use_custom_stoploss=True`

### Backtest results (30 days, 2026-04-11 → 2026-05-11)

Same pair set as the original deep-research baseline (36 pairs incl. CHIP, SAGA), same +26.7% bull-market regime.

| Metric | Baseline | After Parts 1+2 | Delta |
|---|---|---|---|
| Trades | 5 | 4 | −1 |
| Win rate | 60% | **75%** | +15pp ✅ |
| Avg winner | ~1.04% | 1.29% | +0.25pp ✅ |
| Avg loser | −2.02% | **−0.60%** | +1.42pp ✅ |
| **Profit factor** | **1.05** | **1.48** | **+0.43 ✅ clears 1.4 futures-fee bar** |
| Net P&L (on $10k) | +$0.35 | +$2.06 | ~6x ✅ |
| Max drawdown | 0.05% | 0.04% | ~same |

**Strict path: 0 trades.** All 4 trades came from the `momentum` path.

### What's still open

**From Part 1 of the audit:**

- Exchange netflow — skipped (noisy, maintenance-heavy, "Medium" value)
- Token unlock schedules — paid only as of 2026
- Whale wallet flows — paid (Etherscan/Arkham)
- X/Twitter firehose — paid
- Reddit full PRAW — **application submitted 2026-05-12, awaiting approval**.
  Repo `https://github.com/tobbie9217/crypto-bot` and `services/ingest/ingest/collectors/reddit.py` referenced in the application
- CryptoPanic, LunarCrush — user opted out (only Reddit pursued)

**From Part 2 of the audit:**

- Market structure (HH/HL/LL/LH) detection — complex, deferred
- Pivot points / S/R levels — deferred
- Daily/weekly multi-timeframe trend context — infrastructure change, deferred

**From Part 3 (strategy quality, untouched):**

- Mean-reversion entry path for ranging regimes
- Breakout-of-consolidation entry path
- **Strict-path dead — needs decision: relax gates or accept as rare high-conviction path**

**From Part 4 (futures + shorts, untouched):**

- The deep research's prerequisites are now closer: profit factor 1.48 ≥ 1.4 bar.
  Still needs: bear-market historical window, short-trigger logic distinct from
  long, then futures dry-run.

### Next moves (priority order)

1. **Loosen pullback thresholds** — Tier A item. Three-line edit in
   `SentimentOnchainStrategy.py`: `MOMENTUM_PUMP_SIZE_MIN_PCT 8 → 5`,
   `MOMENTUM_PULLBACK_MIN_PCT 2 → 1`, `MOMENTUM_PULLBACK_MAX_PCT 5 → 7`.
   Re-backtest same window. Expected: 8-15 trades, less concentration risk.
2. **60-90 day backtest** on same pair set for statistical confidence.
   4 trades isn't enough to claim victory; the profit-factor jump could
   be partly noise.
3. **Decision on the strict path** — relax one gate (e.g. drop
   `OI_Z_MIN`) and re-backtest, or accept strict as a rare event-trigger
   path.
4. **Wire order-book + liquidation features into the strategy** — Tier
   B item. We collect both now but don't use them. Needs a few days of
   history first so the aggregator has rows to z-score against.
5. **Run the OHLCV backfill** —
   `docker compose run --rm ingest python -m ingest.scripts.backfill_ohlcv`
   — for ~90d 1h + 30d 5m + 7d 1m of historical candles in Postgres.
   ~20 min, idempotent. Not blocking anything, just nice to have.

### Project state at end of session

- All ingest collectors running
- New strategy live in freqtrade (dry-run mode)
- Reddit API application submitted, awaiting decision
- Commit pushed to `origin/main`
