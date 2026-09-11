import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.storage.database import session_scope
from app.storage.models import (
    Base,
    GitHubInstallation,
    Repository,
    ReviewConfig,
    ReviewRun,
    WebhookDelivery,
)


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


def test_webhook_skips_suspended_installation_without_queueing_work(tmp_path: Path) -> None:
    secret = "webhook-secret"
    settings = Settings(
        github_webhook_secret=secret,
        master_key="m" * 32,
        database_url=f"sqlite:///{tmp_path / 'suspended.db'}",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        Base.metadata.create_all(app.state.engine)
        with session_scope(app.state.session_factory) as session:
            installation = GitHubInstallation(
                github_installation_id=1001,
                suspended_at=datetime(2026, 9, 11, tzinfo=UTC),
            )
            repository = Repository(
                github_repository_id=2002,
                installation=installation,
                owner="octo",
                name="repo",
            )
            ReviewConfig(repository=repository)
            session.add(repository)
        body = json.dumps(payload()).encode()
        response = client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": "delivery-suspended",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": signature(body, secret),
            },
        )

        assert response.status_code == 202
        assert response.json()["reason"] == "installation_suspended"
        with session_scope(app.state.session_factory) as session:
            run = session.scalar(select(ReviewRun))
            assert run is not None
            assert run.status == "skipped"
            assert run.failure_code == "installation_suspended"


def test_installation_suspend_preserves_repository_policy_until_delete(tmp_path: Path) -> None:
    secret = "webhook-secret"
    settings = Settings(
        github_webhook_secret=secret,
        master_key="m" * 32,
        database_url=f"sqlite:///{tmp_path / 'installation-lifecycle.db'}",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        Base.metadata.create_all(app.state.engine)
        with session_scope(app.state.session_factory) as session:
            installation = GitHubInstallation(github_installation_id=1001)
            session.add_all(
                [
                    Repository(
                        github_repository_id=2002,
                        installation=installation,
                        owner="octo",
                        name="enabled-repo",
                        enabled=True,
                    ),
                    Repository(
                        github_repository_id=2003,
                        installation=installation,
                        owner="octo",
                        name="disabled-repo",
                        enabled=False,
                    ),
                ]
            )

        def send_installation(action: str, delivery_id: str) -> None:
            body = json.dumps(
                {
                    "action": action,
                    "installation": {
                        "id": 1001,
                        "account": {"login": "octo", "type": "User"},
                    },
                }
            ).encode()
            response = client.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Delivery": delivery_id,
                    "X-GitHub-Event": "installation",
                    "X-Hub-Signature-256": signature(body, secret),
                },
            )
            assert response.status_code == 202

        send_installation("suspend", "delivery-suspend")
        with session_scope(app.state.session_factory) as session:
            installation = session.scalar(select(GitHubInstallation))
            repositories = list(
                session.scalars(select(Repository).order_by(Repository.github_repository_id))
            )
            assert installation is not None
            assert installation.suspended_at is not None
            assert [repository.enabled for repository in repositories] == [True, False]

        send_installation("unsuspend", "delivery-unsuspend")
        with session_scope(app.state.session_factory) as session:
            installation = session.scalar(select(GitHubInstallation))
            repositories = list(
                session.scalars(select(Repository).order_by(Repository.github_repository_id))
            )
            assert installation is not None
            assert installation.suspended_at is None
            assert [repository.enabled for repository in repositories] == [True, False]

        send_installation("deleted", "delivery-deleted")
        with session_scope(app.state.session_factory) as session:
            repositories = list(session.scalars(select(Repository)))
            assert repositories
            assert all(not repository.enabled for repository in repositories)
