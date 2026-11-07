"""Configuration module."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Database
    database_url: str
    database_test_url: str | None = None

    # Redis
    redis_url: str

    # JWT
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Server
    debug: bool = False
    log_level: str = "INFO"

    # Telegram
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    # Worker
    worker_enabled: bool = True
    check_timeout_seconds: int = 10
    incident_threshold: int = 3
    history_retention_days: int = 30
    history_prune_interval_seconds: int = 3600

    # SSRF: extra CIDR ranges to block, comma separated
    ssrf_blocked_networks: str | None = None

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    """Get application settings (cached)."""
    return Settings()
