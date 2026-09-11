from pathlib import Path
from typing import Any

import pytest

from app.github.client import PublishedCheck
from app.github.schemas import GitHubFile, PullRequestData
from app.models.base import FakeModelProvider
from app.notifications.discord import DiscordReview
from app.review.schemas import FindingSchema, ModelReviewOutput, ReviewResult, Severity
from app.security import SecretCipher
from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import (
    Base,
    GitHubCheckDelivery,
    GitHubInstallation,
    NotificationDelivery,
    Repository,
    RepositoryUsage,
    ReviewConfig,
    ReviewRun,
)
from app.worker import ReviewProcessor


class FakeGitHub:
    def __init__(self, head_sha: str = "head", *, fail_check: bool = False) -> None:
        self.head_sha = head_sha
        self.fail_check = fail_check
        self.checks: list[tuple[str, str, str, str, ReviewResult]] = []

    def get_pull(self, owner: str, name: str, pull_number: int) -> PullRequestData:
        del owner, name
        return PullRequestData(
            number=pull_number,
            title="Fix auth",
            author="octo",
            url="https://github.com/octo/repo/pull/7",
            head_sha=self.head_sha,
            base_sha="base",
            files=(
                GitHubFile(
                    path="src/a.py",
                    status="modified",
                    patch="@@ -1 +1 @@\n-old\n+new",
                    changes=2,
                ),
            ),
        )

    def get_head_sha(self, owner: str, name: str, pull_number: int) -> str:
        del owner, name, pull_number
        return self.head_sha

    def publish_review_check(
        self,
        owner: str,
        name: str,
        head_sha: str,
        details_url: str,
        result: ReviewResult,
    ) -> PublishedCheck:
        if self.fail_check:
            raise RuntimeError("check failed")
        self.checks.append((owner, name, head_sha, details_url, result))
        return PublishedCheck(
            id=42,
            url="https://github.com/octo/repo/runs/42",
            conclusion="action_required",
        )


class SupersedingGitHub(FakeGitHub):
    def get_head_sha(self, owner: str, name: str, pull_number: int) -> str:
        del owner, name, pull_number
        return "new-head"


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, DiscordReview]] = []

    def send(self, webhook_url: str, review: DiscordReview) -> list[str]:
        self.sent.append((webhook_url, review))
        return ["message-1"]


@pytest.fixture
def worker_state(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    cipher = SecretCipher("m" * 32)
    with session_scope(factory) as session:
        installation = GitHubInstallation(github_installation_id=1001)
        repository = Repository(
            github_repository_id=2002,
            installation=installation,
            owner="octo",
            name="repo",
        )
        ReviewConfig(
            repository=repository,
            discord_webhook_encrypted=cipher.encrypt("https://discord.com/api/webhooks/123/token"),
        )
        session.add(repository)
        session.flush()
        run = ReviewRun(repository=repository, pull_number=7, head_sha="head")
        session.add(run)
        session.flush()
        run_id = run.id
    yield factory, cipher, run_id
    engine.dispose()


def model_output() -> ModelReviewOutput:
    return ModelReviewOutput(
        summary="Auth changed.",
        risk=Severity.HIGH,
        findings=[
            FindingSchema(
                severity=Severity.HIGH,
                file="src/a.py",
                line=1,
                title="Missing validation",
                explanation="Input is not validated.",
                confidence=0.9,
            )
        ],
    )


def test_worker_completes_review_and_sends_notification(worker_state: Any) -> None:
    factory, cipher, run_id = worker_state
    notifier = FakeNotifier()
    github = FakeGitHub()
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: github,
        FakeModelProvider([model_output()]),
        notifier,
        cipher,
        worker_id="worker-1",
    )
    assert processor.run_once() is True
    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        assert run is not None
        assert run.status == "completed"
        assert len(run.findings) == 1
        assert run.model_calls == 1
        usage = session.query(RepositoryUsage).one()
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reserved_tokens == 0
        assert usage.model_calls == 1
        assert usage.review_runs == 1
        delivery = session.query(NotificationDelivery).one()
        assert delivery.status == "sent"
        assert delivery.message_ids == ["message-1"]
    assert len(notifier.sent) == 1


def test_worker_supersedes_stale_run_without_notification(worker_state: Any) -> None:
    factory, cipher, run_id = worker_state
    notifier = FakeNotifier()
    github = FakeGitHub(head_sha="new-head")
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: github,
        FakeModelProvider([]),
        notifier,
        cipher,
        worker_id="worker-1",
    )
    processor.run_once()
    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        assert run is not None
        assert run.status == "superseded"
    assert notifier.sent == []


def test_worker_settles_usage_when_head_changes_after_model_call(worker_state: Any) -> None:
    factory, cipher, run_id = worker_state
    notifier = FakeNotifier()
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: SupersedingGitHub(),
        FakeModelProvider([model_output()]),
        notifier,
        cipher,
        worker_id="worker-1",
    )

    assert processor.run_once() is True

    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        usage = session.query(RepositoryUsage).one()
        assert run is not None
        assert run.status == "superseded"
        assert run.input_tokens == 100
        assert run.output_tokens == 50
        assert usage.reserved_tokens == 0
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
    assert notifier.sent == []


def test_worker_skips_review_when_monthly_budget_cannot_be_reserved(
    worker_state: Any,
) -> None:
    factory, cipher, run_id = worker_state
    with session_scope(factory) as session:
        config = session.query(ReviewConfig).one()
        config.monthly_token_budget = 1000
    provider = FakeModelProvider([model_output()])
    notifier = FakeNotifier()
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: FakeGitHub(),
        provider,
        notifier,
        cipher,
        worker_id="worker-1",
    )

    assert processor.run_once() is True

    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        assert run is not None
        assert run.status == "skipped"
        assert run.failure_code == "monthly_token_budget_exceeded"
        usage = session.query(RepositoryUsage).one()
        assert usage.reserved_tokens == 0
        assert usage.input_tokens == 0
    assert provider.requests == []
    assert notifier.sent == []


def test_terminal_model_failure_releases_usage_reservation(worker_state: Any) -> None:
    factory, cipher, run_id = worker_state
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: FakeGitHub(),
        FakeModelProvider([]),
        FakeNotifier(),
        cipher,
        worker_id="worker-1",
    )

    assert processor.run_once() is True

    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        usage = session.query(RepositoryUsage).one()
        assert run is not None
        assert run.status == "failed"
        assert run.usage_reservation_tokens == 0
        assert usage.reserved_tokens == 0


def test_worker_publishes_and_records_github_check(worker_state: Any) -> None:
    factory, cipher, run_id = worker_state
    notifier = FakeNotifier()
    github = FakeGitHub()
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: github,
        FakeModelProvider([model_output()]),
        notifier,
        cipher,
        worker_id="worker-1",
        github_checks_enabled=True,
    )

    assert processor.run_once() is True

    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        delivery = session.query(GitHubCheckDelivery).one()
        assert run is not None
        assert run.status == "completed"
        assert delivery.status == "sent"
        assert delivery.github_check_run_id == 42
        assert delivery.conclusion == "action_required"
        assert delivery.details_url == "https://github.com/octo/repo/runs/42"
    assert len(github.checks) == 1


def test_github_check_failure_does_not_lose_review_or_discord_delivery(
    worker_state: Any,
) -> None:
    factory, cipher, run_id = worker_state
    notifier = FakeNotifier()
    github = FakeGitHub(fail_check=True)
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: github,
        FakeModelProvider([model_output()]),
        notifier,
        cipher,
        worker_id="worker-1",
        github_checks_enabled=True,
    )

    assert processor.run_once() is True

    with session_scope(factory) as session:
        run = session.get(ReviewRun, run_id)
        delivery = session.query(GitHubCheckDelivery).one()
        assert run is not None
        assert run.status == "completed"
        assert delivery.status == "failed"
        assert delivery.last_error == "RuntimeError"
    assert len(notifier.sent) == 1


def test_two_repositories_use_different_discord_webhooks(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'two-repositories.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    cipher = SecretCipher("m" * 32)
    expected_webhooks = {
        "https://discord.com/api/webhooks/101/token-one",
        "https://discord.com/api/webhooks/202/token-two",
    }
    with session_scope(factory) as session:
        for index, webhook in enumerate(sorted(expected_webhooks), start=1):
            installation = GitHubInstallation(github_installation_id=1000 + index)
            repository = Repository(
                github_repository_id=2000 + index,
                installation=installation,
                owner="octo",
                name=f"repo-{index}",
            )
            ReviewConfig(
                repository=repository,
                discord_webhook_encrypted=cipher.encrypt(webhook),
            )
            session.add(repository)
            session.flush()
            session.add(ReviewRun(repository=repository, pull_number=7, head_sha="head"))

    notifier = FakeNotifier()
    processor = ReviewProcessor(
        factory,
        lambda _installation_id: FakeGitHub(),
        FakeModelProvider([model_output(), model_output()]),
        notifier,
        cipher,
        worker_id="worker-1",
    )
    assert processor.run_once()
    assert processor.run_once()
    assert {webhook for webhook, _review in notifier.sent} == expected_webhooks
    engine.dispose()
