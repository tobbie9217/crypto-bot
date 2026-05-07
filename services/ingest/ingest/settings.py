from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


settings = Settings()
