# crypto-bot

Sentiment-driven crypto trading bot. Built around Freqtrade with a sentiment
scoring service plugged in as an extra feature.

See `docs/` for week-by-week build notes.

## Architecture

```
[Reddit/CryptoPanic/LunarCrush/Telegram]
              |
              v
         [ingest svc] --writes--> [Postgres]
                                       ^
                                       |
                                  reads/writes
                                       |
                                  [sentiment svc (CryptoBERT)]
                                       ^
                                       | reads aggregates
                                       |
[Telegram UI] <-- [freqtrade] <-- [SentimentStrategy] reads Postgres
                       |
                       v
                  [Binance via CCXT]
```

Five containers in `docker-compose.yml`:
- **postgres** — single source of truth (posts, scores, aggregates, events)
- **ingest** — polls social/news APIs, writes to `posts`
- **sentiment** — scores posts with CryptoBERT, writes to `sentiment_scores` and rolls up to `sentiment_aggregates` (week 3)
- **freqtrade** — the trading engine, with Telegram control built in
- **telegram-collector** — separate Telethon listener for alpha channels (week 7)

## Quickstart

### Prerequisites
- Docker Desktop for Windows (with WSL2 backend)
- Telegram account
- Reddit account (free; needed for week 2)

### One-time setup

1. **Create your Telegram bot**
   - Message `@BotFather`, send `/newbot`, follow prompts -> save the token.
   - Send any message to your bot, then visit
     `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id`.

2. **Configure secrets**
   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```
   Fill in at minimum: `POSTGRES_PASSWORD`, `FREQTRADE__TELEGRAM__TOKEN`,
   `FREQTRADE__TELEGRAM__CHAT_ID`, and a strong `FREQTRADE__API_SERVER__PASSWORD`.

3. **Start it**
   ```powershell
   docker compose up -d
   docker compose logs -f
   ```

4. **Verify**
   - Telegram: send `/status` to your bot.
   - Web UI: http://localhost:8080
   - Postgres: `docker compose exec postgres psql -U cryptobot -d cryptobot`

## Roadmap

| Week | Doc                                                           | Deliverable |
|------|---------------------------------------------------------------|-------------|
| 1    | (this README)                                                 | Freqtrade + Telegram, dry-run |
| 2    | [docs/week2-data-ingestion.md](docs/week2-data-ingestion.md)  | Reddit/CryptoPanic/LunarCrush -> Postgres |
| 3    | [docs/week3-sentiment.md](docs/week3-sentiment.md)            | CryptoBERT scoring + aggregates |
| 4    | [docs/week4-strategy.md](docs/week4-strategy.md)              | SentimentStrategy + walk-forward backtest |
| 5    | [docs/week5-risk.md](docs/week5-risk.md)                      | Stop-loss, kill-switch, position sizing |
| 6    | [docs/week6-papertrade.md](docs/week6-papertrade.md)          | Paper-trade on Binance testnet, $10K virtual |
| 7    | [docs/week7-telegram-channels.md](docs/week7-telegram-channels.md) | Add Telegram alpha-channel ingestion |
| 8    | [docs/week8-live.md](docs/week8-live.md)                      | Live trade with $100-500, tight risk caps |

## Stopping / cleaning

```powershell
docker compose down            # stop, keep data
docker compose down -v         # also delete Postgres volume (destroys posts/scores)
```
