import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.storage.database import session_scope
from app.storage.models import Base, ReviewRun, WebhookDelivery


def payload() -> dict[str, object]:
    return {
        "action": "opened",
        "number": 7,
        "installation": {"id": 1001},
        "repository": {
            "id": 2002,
            "name": "repo",
            "owner": {"login": "octo"},
        },
        "pull_request": {
            "draft": False,
            "head": {"sha": "head"},
            "base": {"sha": "base"},
        },
    }


def signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_enqueues_once_and_returns_202(tmp_path: Path) -> None:
    secret = "webhook-secret"
    settings = Settings(
        github_webhook_secret=secret,
        master_key="m" * 32,
        database_url=f"sqlite:///{tmp_path / 'webhook.db'}",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        Base.metadata.create_all(app.state.engine)
        body = json.dumps(payload()).encode()
        headers = {
            "Content-Type": "application/json",
            "X-GitHub-Delivery": "delivery-1",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature(body, secret),
        }
        first = client.post("/api/webhooks/github", content=body, headers=headers)
        second = client.post("/api/webhooks/github", content=body, headers=headers)
        assert first.status_code == 202
        assert first.json()["review_run_id"] is not None
        assert second.status_code == 202
        assert second.json()["duplicate"] is True
        with session_scope(app.state.session_factory) as session:
            assert session.scalar(select(func.count()).select_from(WebhookDelivery)) == 1
            assert session.scalar(select(func.count()).select_from(ReviewRun)) == 1
            run = session.scalar(select(ReviewRun))
            assert run is not None
            assert run.config_snapshot is not None
            assert run.config_snapshot["max_output_tokens_per_call"] == 4000
            assert run.config_snapshot["monthly_token_budget"] == 1_000_000


def test_webhook_rejects_bad_signature(tmp_path: Path) -> None:
    settings = Settings(
        github_webhook_secret="secret",
        master_key="m" * 32,
        database_url=f"sqlite:///{tmp_path / 'bad-signature.db'}",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/webhooks/github",
            json=payload(),
            headers={
                "X-GitHub-Delivery": "delivery-1",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=wrong",
            },
        )
    assert response.status_code == 403
