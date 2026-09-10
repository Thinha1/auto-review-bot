from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import Base, GitHubInstallation, Repository, ReviewRun
from app.storage.queue import ReviewQueue


@pytest.fixture
def queue_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        installation = GitHubInstallation(github_installation_id=1)
        repository = Repository(
            github_repository_id=2,
            installation=installation,
            owner="octo",
            name="repo",
        )
        session.add(repository)
        session.flush()
        session.add(ReviewRun(repository=repository, pull_number=1, head_sha="head"))
    yield factory
    engine.dispose()


def test_only_one_worker_claims_a_run(queue_factory: Any) -> None:
    with session_scope(queue_factory) as session:
        first = ReviewQueue(session).claim_next("worker-1")
        assert first is not None
    with session_scope(queue_factory) as session:
        second = ReviewQueue(session).claim_next("worker-2")
        assert second is None


def test_expired_lease_is_reclaimed(queue_factory: Any) -> None:
    old = datetime.now(UTC) - timedelta(minutes=10)
    with session_scope(queue_factory) as session:
        run = ReviewQueue(session).claim_next("dead-worker", lease_seconds=1, now=old)
        assert run is not None
    with session_scope(queue_factory) as session:
        reclaimed = ReviewQueue(session).claim_next("live-worker", now=datetime.now(UTC))
        assert reclaimed is not None
        assert reclaimed.locked_by == "live-worker"
        assert reclaimed.attempt_count == 2
