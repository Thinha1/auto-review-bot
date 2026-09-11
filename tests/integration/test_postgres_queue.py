import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.application.usage import UsageService
from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import GitHubInstallation, Repository, ReviewRun
from app.storage.queue import ReviewQueue
from app.storage.usage import SQLAlchemyUsageBudgetStore

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


def test_concurrent_usage_reservations_cannot_exceed_budget() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    factory = make_session_factory(engine)
    unique = uuid4().int % 1_000_000_000
    with session_scope(factory) as session:
        installation = GitHubInstallation(github_installation_id=10_000_000_000 + unique)
        repository = Repository(
            github_repository_id=11_000_000_000 + unique,
            installation=installation,
            owner="usage-test",
            name=str(unique),
        )
        session.add(repository)
        session.flush()
        runs = [
            ReviewRun(
                repository=repository,
                pull_number=pull_number,
                head_sha=f"usage-{unique}-{pull_number}",
            )
            for pull_number in (1, 2)
        ]
        session.add_all(runs)
        session.flush()
        repository_id = repository.id
        run_ids = [run.id for run in runs]

    def reserve(run_id: int) -> bool:
        with session_scope(factory) as session:
            return UsageService(SQLAlchemyUsageBudgetStore(session)).reserve(
                run_id, 600, token_budget=1000
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, run_ids))

    with session_scope(factory) as session:
        usage = UsageService(SQLAlchemyUsageBudgetStore(session)).snapshot(
            repository_id, token_budget=1000
        )
        assert sorted(results) == [False, True]
        assert usage.reserved_tokens == 600
        assert usage.remaining_tokens == 400
    engine.dispose()
