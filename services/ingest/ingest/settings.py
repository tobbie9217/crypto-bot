from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        return None if v == "" else v

    database_url: str

    # Reddit: https://www.reddit.com/prefs/apps -> create a "script" app
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "crypto-bot:v0.1"
    reddit_subreddits: str = "cryptocurrency,bitcoin,cryptomarkets"

    # CryptoPanic: https://cryptopanic.com/developers/api/
    cryptopanic_token: str | None = None

    # LunarCrush: https://lunarcrush.com/developers
    lunarcrush_token: str | None = None

    # CryptoCompare: https://www.cryptocompare.com/cryptopian/api-keys (free tier)
    cryptocompare_api_key: str | None = None

    # Telegram (week 7) — fill in once you've run the auth flow once
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str = "/app/sessions/telegram.session"
    telegram_channels: str = ""

    # Coins we care about
    coins: str = "BTC,ETH,SOL,BNB"

    # Polling cadence (seconds). Reddit free tier = 100 RPM, so 60s/sub is safe.
    reddit_interval_s: int = 60
    cryptopanic_interval_s: int = 120
    lunarcrush_interval_s: int = 60

    # RSS-based collectors (no API keys required)
    news_rss_feeds: str = ""  # comma-separated; empty = use defaults from collector
    news_rss_interval_s: int = 600
    reddit_rss_interval_s: int = 120

    # Free, no-key collectors added in the on-chain expansion
    cryptocompare_news_interval_s: int = 1800   # 30 min — ~50 articles per call
    coingecko_market_interval_s: int = 300      # 5 min — well under free-tier limits
    defillama_interval_s: int = 600             # 10 min

    # Phase C: derivatives + market-wide signals
    binance_derivatives_interval_s: int = 300   # 5 min — funding + mark + OI
    fear_greed_interval_s: int = 1800           # 30 min — F&G updates daily

    # Binance-native swap-ins (replace CoinGecko market) + new positioning ratios
    binance_spot_interval_s: int = 300          # 5 min — price/volume/24h-change
    binance_ratios_interval_s: int = 600        # 10 min — top/global LSR + taker ratio
    binance_listings_interval_s: int = 1800     # 30 min — detect new symbol listings

    log_level: str = "INFO"

    @property
    def reddit_subreddits_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    @property
    def coins_list(self) -> list[str]:
        return [c.strip().upper() for c in self.coins.split(",") if c.strip()]

    @property
    def telegram_channels_list(self) -> list[str]:
        return [c.strip() for c in self.telegram_channels.split(",") if c.strip()]

    @property
    def news_rss_feeds_list(self) -> list[str]:
        return [u.strip() for u in self.news_rss_feeds.split(",") if u.strip()]


settings = Settings()
