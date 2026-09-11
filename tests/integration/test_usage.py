from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.application.usage import UsageService
from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import Base, GitHubInstallation, Repository, ReviewRun
from app.storage.usage import SQLAlchemyUsageBudgetStore


@pytest.fixture
def usage_state(tmp_path: Path) -> Generator[tuple[Any, int, int, int], None, None]:
    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        installation = GitHubInstallation(github_installation_id=1001)
        repository = Repository(
            github_repository_id=2002,
            installation=installation,
            owner="octo",
            name="repo",
        )
        session.add(repository)
        session.flush()
        first = ReviewRun(repository=repository, pull_number=1, head_sha="one")
        second = ReviewRun(repository=repository, pull_number=2, head_sha="two")
        session.add_all([first, second])
        session.flush()
        state = factory, repository.id, first.id, second.id
    yield state
    engine.dispose()


def test_reservation_is_idempotent_and_settles_actual_usage(usage_state: Any) -> None:
    factory, repository_id, first_id, second_id = usage_state
    now = datetime(2026, 9, 10, tzinfo=UTC)
    with session_scope(factory) as session:
        budget = UsageService(SQLAlchemyUsageBudgetStore(session))
        assert budget.reserve(first_id, 600, token_budget=1000, now=now)
        assert budget.reserve(first_id, 600, token_budget=1000, now=now)
        assert budget.reserve(second_id, 500, token_budget=1000, now=now) is False
        assert budget.snapshot(repository_id, token_budget=1000, now=now).reserved_tokens == 600

    with session_scope(factory) as session:
        budget = UsageService(SQLAlchemyUsageBudgetStore(session))
        budget.settle(
            first_id,
            input_tokens=300,
            output_tokens=100,
            model_calls=2,
            now=now,
        )
        assert budget.reserve(second_id, 500, token_budget=1000, now=now)
        usage = budget.snapshot(repository_id, token_budget=1000, now=now)
        assert usage.used_tokens == 400
        assert usage.reserved_tokens == 500
        assert usage.remaining_tokens == 100
        assert usage.model_calls == 2
        assert usage.review_runs == 1


def test_terminal_failure_releases_reservation(usage_state: Any) -> None:
    factory, repository_id, first_id, _second_id = usage_state
    now = datetime(2026, 9, 10, tzinfo=UTC)
    with session_scope(factory) as session:
        budget = UsageService(SQLAlchemyUsageBudgetStore(session))
        assert budget.reserve(first_id, 600, token_budget=1000, now=now)
        budget.release(first_id, now=now)
        usage = budget.snapshot(repository_id, token_budget=1000, now=now)
        assert usage.reserved_tokens == 0
        assert usage.used_tokens == 0


def test_usage_rolls_over_at_utc_month_boundary(usage_state: Any) -> None:
    factory, repository_id, first_id, _second_id = usage_state
    september = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
    october = datetime(2026, 10, 1, tzinfo=UTC)
    with session_scope(factory) as session:
        budget = UsageService(SQLAlchemyUsageBudgetStore(session))
        assert budget.reserve(first_id, 600, token_budget=1000, now=september)
        budget.settle(
            first_id,
            input_tokens=300,
            output_tokens=100,
            model_calls=1,
            now=september,
        )
        assert budget.snapshot(repository_id, token_budget=1000, now=september).used_tokens == 400
        october_usage = budget.snapshot(repository_id, token_budget=1000, now=october)
        assert october_usage.used_tokens == 0
        assert october_usage.remaining_tokens == 1000
