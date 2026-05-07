# crypto-bot

Sentiment-driven crypto trading bot. Built around Freqtrade with a sentiment
scoring service plugged in as an extra feature.

See `docs/` for the full architecture. This README covers week-1 quickstart.

## Architecture (target)

```
[Data ingestion] -> [Sentiment engine] -> [Strategy/risk engine]
                                                  |
[Telegram UI] <- [Execution engine (Binance/CCXT)] <-+
                          |
                          v
                  [Audit log + replay store]
```

Week 1 only stands up the **execution engine + Telegram UI** in dry-run mode.
Sentiment ingestion comes in week 2.

## Quickstart (week 1)

### Prerequisites
- Docker Desktop for Windows (with WSL2 backend)
- A Telegram account

### One-time setup

1. **Create your Telegram bot**
   - Open Telegram, message `@BotFather`, send `/newbot`, follow prompts.
   - Save the **bot token** it gives you.
   - Send any message to your new bot, then visit
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser
     and copy the `chat.id` number.

2. **Configure secrets**
   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```
   Fill in `FREQTRADE__TELEGRAM__TOKEN`, `FREQTRADE__TELEGRAM__CHAT_ID`,
   and a password + random JWT secret for the web UI.

3. **Run it**
   ```powershell
   docker compose up -d
   docker compose logs -f freqtrade
   ```

4. **Verify**
   - Telegram: send `/status` to your bot, it should reply.
   - Web UI: http://localhost:8080 (login with the creds from `.env`).

### Stopping
```powershell
docker compose down
```

## What's running

- **freqtrade** container in `dry_run` mode — paper-trades 4 pairs
  (BTC/ETH/SOL/BNB-USDT) on a simple RSI strategy using live Binance
  market data. No real money, no exchange API keys needed yet.

## Roadmap

| Week | Deliverable |
|------|-------------|
| 1    | Freqtrade + Telegram control (dry-run)                   |
| 2    | Data ingestion: LunarCrush + CryptoPanic + Reddit -> Postgres |
| 3    | CryptoBERT scoring service, 6mo historical backfill       |
| 4    | Strategy v1: sentiment z-score + RSI + EMA, walk-forward backtest |
| 5    | Risk module: stop-loss, position sizing, kill-switch     |
| 6    | Paper-trade on Binance testnet, $10K virtual              |
| 7    | Iterate on failures, add Telegram alpha-channel ingestion |
| 8    | Live trade with $100-500, tight risk caps                 |
