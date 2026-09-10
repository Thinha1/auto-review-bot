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
    assert s.database_url.startswith("sqlite")


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
