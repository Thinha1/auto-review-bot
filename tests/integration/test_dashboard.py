import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.authz import RepositoryRole
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
    def __init__(self, role: RepositoryRole | None = RepositoryRole.ADMIN) -> None:
        self.role = role

    def repository_role(self, access_token: str, owner: str, name: str) -> RepositoryRole | None:
        if access_token == "oauth-token" and owner == "octo" and name == "repo":
            return self.role
        return None


def build_dashboard(
    tmp_path: Path, *, role: RepositoryRole | None = RepositoryRole.ADMIN
) -> tuple[TestClient, Any, int]:
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
    app.state.oauth_client = FakeOAuth(role)
    Base.metadata.create_all(app.state.engine)
    raw_token = "dashboard-cookie"
    with session_scope(app.state.session_factory) as session:
        installation = GitHubInstallation(
            github_installation_id=1,
            account_login="octo",
            account_type="Organization",
        )
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


def test_dashboard_lists_accessible_organization_repositories_with_role(tmp_path: Path) -> None:
    client, _app, _repository_id = build_dashboard(tmp_path)
    try:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "octo/repo" in response.text
        assert "admin" in response.text
    finally:
        client.__exit__(None, None, None)


def test_user_without_repository_access_cannot_open_repository(tmp_path: Path) -> None:
    client, _app, repository_id = build_dashboard(tmp_path, role=None)
    try:
        response = client.get(f"/dashboard/repositories/{repository_id}")
        assert response.status_code == 403
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("role", [RepositoryRole.VIEWER, RepositoryRole.MAINTAINER])
def test_non_admin_can_read_repository_but_cannot_change_policy(
    tmp_path: Path, role: RepositoryRole
) -> None:
    client, _app, repository_id = build_dashboard(tmp_path, role=role)
    try:
        page = client.get(f"/dashboard/repositories/{repository_id}")
        assert page.status_code == 200
        assert role.value in page.text
        assert "Read only" in page.text
        assert "Save settings" not in page.text

        response = client.post(
            f"/dashboard/repositories/{repository_id}",
            data={"csrf_token": "csrf-token", "model": "forbidden-model"},
        )
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
    client, app, repository_id = build_dashboard(tmp_path, role=RepositoryRole.MAINTAINER)
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


def test_viewer_can_read_run_but_cannot_retry_it(tmp_path: Path) -> None:
    client, app, repository_id = build_dashboard(tmp_path, role=RepositoryRole.VIEWER)
    with session_scope(app.state.session_factory) as session:
        run = ReviewRun(
            repository_id=repository_id,
            pull_number=7,
            head_sha="head",
            status="failed",
        )
        session.add(run)
        session.flush()
        run_id = run.id
    try:
        page = client.get(f"/dashboard/runs/{run_id}")
        assert page.status_code == 200
        assert "viewer" in page.text

        response = client.post(
            f"/dashboard/runs/{run_id}/retry",
            data={"csrf_token": "csrf-token"},
        )
        assert response.status_code == 403
    finally:
        client.__exit__(None, None, None)
