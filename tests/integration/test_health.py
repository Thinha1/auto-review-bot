"""Integration tests for the HTTP API surface."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_fail_fast_when_secrets_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="settings"), TestClient(create_app()) as test_client:
        test_client.get("/healthz")
