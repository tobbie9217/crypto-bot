# Week 6 — Paper-trade on Binance testnet

By now you've been "dry-running" — Freqtrade simulating fills against
**live mainnet market data** with no exchange account at all. Week 6 is
the next rung up: route the same dry-run logic through **Binance's testnet
order book** so the fills are simulated by Binance's matching engine
instead of by Freqtrade. Closer to live mechanics, still no real money.

Goal: 30 days of clean operation with positive expectancy and
drawdown < 10%. If you can't get there in paper, you do not go live.

## Set up Binance testnet

1. Visit https://testnet.binance.vision (spot testnet).
2. Log in with **GitHub** (the testnet doesn't accept normal Binance
   accounts — separate auth on purpose).
3. Click **Generate HMAC_SHA256 Key**. Save the **API Key** and **Secret Key**.
4. The testnet seeds your account with fake balances (BTC, ETH, USDT, etc.).
   Use them — they reset periodically.

## Tell Freqtrade to use testnet

Add to `.env`:

```
FREQTRADE__EXCHANGE__KEY=your_testnet_api_key
FREQTRADE__EXCHANGE__SECRET=your_testnet_secret_key
```

And edit `user_data/config.json`:

```json
"exchange": {
    "name": "binance",
    "sandbox": true,        // <-- add this line
    "key": "",
    "secret": "",
    ...
}
```

The `sandbox: true` flag tells CCXT to point at `testnet.binance.vision`
instead of `api.binance.com`.

Restart freqtrade:
```powershell
docker compose up -d --force-recreate freqtrade
```

## Verify

In Telegram:
```
/balance        -> should show your testnet balances (not the dry_run_wallet)
/status         -> live status of paper trades
/profit         -> running P/L
```

In FreqUI (http://localhost:8080) the **Exchange** tab should read
`binance (testnet)`.

## Run for 30 days

Set a calendar reminder. Track in a spreadsheet or just from `/profit`:
- Daily P&L
- Open/closed trade count
- Max drawdown so far
- Profit factor (gross profit / gross loss)
- Win rate

## Decision gates before week 8 (live)

| Metric | Required | Why |
|--------|----------|-----|
| Days running | >= 30 | Need varied market regimes (trend, chop, dump) |
| Profit factor | >= 1.5 | Doc's threshold — below this, edge is too thin |
| Win rate | 55-70% | Below 55 = strategy is bad. Above 70 = suspect look-ahead bias |
| Max drawdown | < 10% | If you can't tolerate paper losses, real losses will be worse |
| Bugs/crashes | 0 unaddressed | Telegram silent? DB errors in logs? Fix before live |

If any gate fails, **don't go live**. Iterate (week 7) and re-run paper
trade. The bots that survive are the ones whose authors had the patience
to fail in paper instead of in production.

## Common issues

- **`Invalid API-key, IP, or permissions for action`** — testnet keys
  expire and reset periodically. Regenerate and update `.env`.
- **Orders never fill** — testnet liquidity is thin. Try a more popular
  pair (BTC/USDT) before assuming the strategy is broken.
- **Wallet shows zero** — your testnet balance got reset. Just regenerate
  keys; the new ones will come with fresh test funds.
