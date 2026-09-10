"""Shared pytest fixtures."""

import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Isolate the lru-cached settings across tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def settings() -> Settings:
    """A valid Settings instance for building the app in tests."""
    return Settings(github_webhook_secret="test-secret", master_key="test-master-key")
