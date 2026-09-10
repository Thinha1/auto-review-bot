from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.domain.states import InvalidStateTransitionError, ReviewRunStatus
from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import Base, GitHubInstallation, Repository, ReviewRun, WebhookDelivery
from app.storage.repositories import (
    RepositoryRepository,
    ReviewRunRepository,
    WebhookDeliveryRepository,
)


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def add_repository(session: Any) -> Repository:
    installation = GitHubInstallation(github_installation_id=1001)
    repository = Repository(
        github_repository_id=2002,
        installation=installation,
        owner="octo",
        name="repo",
    )
    session.add(repository)
    session.flush()
    return repository


def test_webhook_delivery_is_idempotent(session_factory: Any) -> None:
    with session_scope(session_factory) as session:
        repository = WebhookDeliveryRepository(session)
        first, created_first = repository.record_if_new(
            github_delivery_id="delivery-1", event="pull_request", action="opened"
        )
        second, created_second = repository.record_if_new(
            github_delivery_id="delivery-1", event="pull_request", action="opened"
        )
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(WebhookDelivery)) == 1


def test_review_run_is_idempotent(session_factory: Any) -> None:
    with session_scope(session_factory) as session:
        repo = add_repository(session)
        runs = ReviewRunRepository(session)
        first, created_first = runs.create_if_new(
            repository_id=repo.id, pull_number=7, head_sha="abc"
        )
        second, created_second = runs.create_if_new(
            repository_id=repo.id, pull_number=7, head_sha="abc"
        )
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(ReviewRun)) == 1


def test_invalid_status_transition_is_rejected(session_factory: Any) -> None:
    with session_scope(session_factory) as session:
        repo = add_repository(session)
        runs = ReviewRunRepository(session)
        run, _ = runs.create_if_new(repository_id=repo.id, pull_number=7, head_sha="abc")
        runs.set_status(run.id, ReviewRunStatus.RUNNING)
        runs.set_status(run.id, ReviewRunStatus.COMPLETED)
        with pytest.raises(InvalidStateTransitionError):
            runs.set_status(run.id, ReviewRunStatus.RUNNING)


def test_repository_rename_keeps_stable_github_identity(session_factory: Any) -> None:
    with session_scope(session_factory) as session:
        repo = add_repository(session)
        repositories = RepositoryRepository(session)
        repo.owner = "new-owner"
        repo.name = "new-name"
        session.flush()
        found = repositories.get_by_github_id(2002)
        assert found is not None
        assert found.id == repo.id
        assert found.owner == "new-owner"
