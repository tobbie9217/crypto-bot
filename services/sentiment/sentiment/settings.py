from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    model_name: str = "ElKulako/cryptobert"
    batch_size: int = 32
    idle_sleep_s: int = 30      # how long to wait when the queue is empty
    max_text_tokens: int = 256
    log_level: str = "INFO"


settings = Settings()
