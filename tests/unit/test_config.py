"""Unit tests for app.config."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_from_explicit_values():
    s = Settings(github_webhook_secret="abc", master_key="def")
    assert s.github_webhook_secret == "abc"
    assert s.master_key == "def"
    assert s.app_debug is False
    assert s.github_checks_enabled is False
    assert s.openai_api_style == "responses"
    assert s.openai_chat_response_format == "json_schema"
    assert s.openai_chat_token_limit_field == "max_completion_tokens"
    assert s.database_url.startswith("sqlite")
    assert s.worker_metrics_host == "127.0.0.1"
    assert s.worker_metrics_port == 9100


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
    monkeypatch.setenv("MASTER_KEY", "env-key")
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("GITHUB_CHECKS_ENABLED", "true")
    s = Settings()  # pyright: ignore[reportCallIssue]
    assert s.github_webhook_secret == "env-secret"
    assert s.master_key == "env-key"
    assert s.app_debug is True
    assert s.github_checks_enabled is True


def test_missing_required_secret_fails(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("MASTER_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # pyright: ignore[reportCallIssue]


def test_openai_compatibility_modes_are_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(
            github_webhook_secret="secret",
            master_key="m" * 32,
            openai_api_style="unsupported",  # type: ignore[arg-type]
        )
