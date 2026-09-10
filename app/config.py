"""Application settings loaded from the environment and `.env`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite:///./auto_review.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "auto-review-discord"
    # Named ``app_debug`` (env ``APP_DEBUG``) to avoid colliding with the
    # widely-used global ``DEBUG`` variable that other tooling sets.
    app_debug: bool = False
    database_url: str = DEFAULT_DATABASE_URL

    # Required secrets. The app fails fast at startup when these are missing.
    github_webhook_secret: str
    master_key: str


@lru_cache
def get_settings() -> Settings:
    # Required fields are provided via environment / `.env`; pydantic-settings
    # resolves them at runtime, so the empty call is valid despite no defaults.
    return Settings()  # pyright: ignore[reportCallIssue]
