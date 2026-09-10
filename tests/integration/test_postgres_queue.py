import os
from uuid import uuid4

import pytest

from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import GitHubInstallation, Repository, ReviewRun
from app.storage.queue import ReviewQueue

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured")


def test_workers_claim_different_rows_without_waiting() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    factory = make_session_factory(engine)
    unique = uuid4().int % 1_000_000_000
    run_ids: list[int] = []
    with session_scope(factory) as session:
        installation = GitHubInstallation(github_installation_id=8_000_000_000 + unique)
        repository = Repository(
            github_repository_id=9_000_000_000 + unique,
            installation=installation,
            owner="queue-test",
            name=str(unique),
        )
        session.add(repository)
        session.flush()
        for pull_number in (1, 2):
            run = ReviewRun(
                repository=repository,
                pull_number=pull_number,
                head_sha=f"head-{unique}-{pull_number}",
            )
            session.add(run)
            session.flush()
            run_ids.append(run.id)

    first_session = factory()
    second_session = factory()
    try:
        first = ReviewQueue(first_session).claim_next("worker-one")
        assert first is not None
        second = ReviewQueue(second_session).claim_next("worker-two")
        assert second is not None
        assert {first.id, second.id} == set(run_ids)
    finally:
        first_session.rollback()
        second_session.rollback()
        first_session.close()
        second_session.close()
        engine.dispose()
