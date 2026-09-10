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

    github_app_id: str | None = None
    github_private_key: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_app_slug: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    github_api_url: str = "https://api.github.com"
    github_checks_enabled: bool = False
    session_secret: str | None = None
    public_base_url: str = "http://127.0.0.1:8000"
    worker_poll_seconds: float = 2.0
    worker_lease_seconds: int = 300
    worker_max_attempts: int = 3


@lru_cache
def get_settings() -> Settings:
    # Required fields are provided via environment / `.env`; pydantic-settings
    # resolves them at runtime, so the empty call is valid despite no defaults.
    return Settings()  # pyright: ignore[reportCallIssue]
