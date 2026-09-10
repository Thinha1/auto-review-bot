import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import SecretCipher
from app.storage.database import session_scope
from app.storage.models import (
    Base,
    DashboardSession,
    GitHubInstallation,
    Repository,
    ReviewConfig,
    ReviewRun,
)


class FakeOAuth:
    def __init__(self, admin: bool = True) -> None:
        self.admin = admin

    def is_repository_admin(self, access_token: str, owner: str, name: str) -> bool:
        return self.admin and access_token == "oauth-token" and owner == "octo" and name == "repo"


def build_dashboard(tmp_path: Path, *, admin: bool = True) -> tuple[TestClient, Any, int]:
    settings = Settings(
        github_webhook_secret="secret",
        master_key="m" * 32,
        github_client_id="client",
        github_client_secret="client-secret",
        database_url=f"sqlite:///{tmp_path / 'dashboard.db'}",
    )
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.oauth_client = FakeOAuth(admin)
    Base.metadata.create_all(app.state.engine)
    raw_token = "dashboard-cookie"
    with session_scope(app.state.session_factory) as session:
        installation = GitHubInstallation(github_installation_id=1)
        repository = Repository(
            github_repository_id=2,
            installation=installation,
            owner="octo",
            name="repo",
        )
        ReviewConfig(repository=repository)
        session.add(repository)
        session.flush()
        repository_id = repository.id
        session.add(
            DashboardSession(
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                github_login="octo",
                access_token_encrypted=SecretCipher("m" * 32).encrypt("oauth-token"),
                csrf_token="csrf-token",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    client.cookies.set("pr_review_session", raw_token)
    return client, app, repository_id


def test_dashboard_only_lists_administered_repositories(tmp_path: Path) -> None:
    client, _app, _repository_id = build_dashboard(tmp_path)
    try:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "octo/repo" in response.text
    finally:
        client.__exit__(None, None, None)


def test_non_admin_cannot_open_repository_settings(tmp_path: Path) -> None:
    client, _app, repository_id = build_dashboard(tmp_path, admin=False)
    try:
        response = client.get(f"/dashboard/repositories/{repository_id}")
        assert response.status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_settings_update_encrypts_write_only_webhook(tmp_path: Path) -> None:
    client, app, repository_id = build_dashboard(tmp_path)
    webhook = "https://discord.com/api/webhooks/123/super-secret-token"
    try:
        response = client.post(
            f"/dashboard/repositories/{repository_id}",
            data={
                "csrf_token": "csrf-token",
                "enabled": "on",
                "ignore_drafts": "on",
                "model": "gpt-test",
                "minimum_severity": "high",
                "max_files": "20",
                "max_diff_lines": "1000",
                "max_findings": "10",
                "max_input_tokens": "20000",
                "discord_webhook": webhook,
                "ignored_paths": "docs/*\n*.snap",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with session_scope(app.state.session_factory) as session:
            config = session.query(ReviewConfig).one()
            assert config.discord_webhook_encrypted is not None
            assert "super-secret-token" not in config.discord_webhook_encrypted
            assert config.minimum_severity == "high"
            assert config.ignored_paths == ["docs/*", "*.snap"]
        page = client.get(f"/dashboard/repositories/{repository_id}")
        assert webhook not in page.text
        assert "configured" in page.text
    finally:
        client.__exit__(None, None, None)


def test_failed_run_can_be_requeued_with_csrf(tmp_path: Path) -> None:
    client, app, repository_id = build_dashboard(tmp_path)
    with session_scope(app.state.session_factory) as session:
        run = ReviewRun(
            repository_id=repository_id,
            pull_number=7,
            head_sha="head",
            status="failed",
            attempt_count=3,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    try:
        response = client.post(
            f"/dashboard/runs/{run_id}/retry",
            data={"csrf_token": "csrf-token"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        with session_scope(app.state.session_factory) as session:
            run = session.get(ReviewRun, run_id)
            assert run is not None
            assert run.status == "queued"
            assert run.attempt_count == 0
    finally:
        client.__exit__(None, None, None)
