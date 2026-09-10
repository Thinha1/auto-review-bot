from pathlib import Path
from typing import Any

import pytest

from app.github.schemas import GitHubFile, PullRequestData
from app.models.base import FakeModelProvider
from app.notifications.discord import DiscordReview
from app.review.schemas import FindingSchema, ModelReviewOutput, Severity
from app.security import SecretCipher
from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import (
    Base,
    GitHubInstallation,
    NotificationDelivery,
    Repository,
    ReviewConfig,
    ReviewRun,
)
from app.worker import ReviewProcessor


class FakeGitHub:
    def __init__(self, head_sha: str = "head") -> None:
        self.head_sha = head_sha

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
