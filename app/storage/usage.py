"""SQLAlchemy implementation of atomic repository usage persistence."""

from datetime import UTC, date, datetime

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.application.usage import UsageSnapshot, monthly_period_start
from app.storage.models import RepositoryUsage, ReviewRun


class SQLAlchemyUsageBudgetStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def reserve(
        self,
        review_run_id: int,
        tokens: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> bool:
        run = self._run(review_run_id)
        if run.usage_settled_at is not None:
            raise ValueError("Review run usage is already settled")
        if run.usage_period_start is not None:
            return True

        current = now or datetime.now(UTC)
        period = monthly_period_start(current)
        self._ensure_period(run.repository_id, period, current)
        result = self.session.execute(
            update(RepositoryUsage)
            .where(
                RepositoryUsage.repository_id == run.repository_id,
                RepositoryUsage.period_start == period,
                RepositoryUsage.input_tokens
                + RepositoryUsage.output_tokens
                + RepositoryUsage.reserved_tokens
                + tokens
                <= token_budget,
            )
            .values(
                reserved_tokens=RepositoryUsage.reserved_tokens + tokens,
                updated_at=current,
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            return False
        run.usage_period_start = period
        run.usage_reservation_tokens = tokens
        self.session.flush()
        return True

    def settle(
        self,
        review_run_id: int,
        *,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        now: datetime | None = None,
    ) -> None:
        run = self._run(review_run_id)
        if run.usage_settled_at is not None:
            return
        if run.usage_period_start is None:
            raise ValueError("Review run has no usage reservation")
        current = now or datetime.now(UTC)
        reserved = run.usage_reservation_tokens
        result = self.session.execute(
            update(RepositoryUsage)
            .where(
                RepositoryUsage.repository_id == run.repository_id,
                RepositoryUsage.period_start == run.usage_period_start,
            )
            .values(
                reserved_tokens=case(
                    (
                        RepositoryUsage.reserved_tokens >= reserved,
                        RepositoryUsage.reserved_tokens - reserved,
                    ),
                    else_=0,
                ),
                input_tokens=RepositoryUsage.input_tokens + input_tokens,
                output_tokens=RepositoryUsage.output_tokens + output_tokens,
                model_calls=RepositoryUsage.model_calls + model_calls,
                review_runs=RepositoryUsage.review_runs + 1,
                updated_at=current,
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise LookupError("Usage period does not exist")
        run.usage_reservation_tokens = 0
        run.usage_settled_at = current
        self.session.flush()

    def release(self, review_run_id: int, *, now: datetime | None = None) -> None:
        run = self._run(review_run_id)
        if run.usage_settled_at is not None or run.usage_period_start is None:
            return
        current = now or datetime.now(UTC)
        reserved = run.usage_reservation_tokens
        result = self.session.execute(
            update(RepositoryUsage)
            .where(
                RepositoryUsage.repository_id == run.repository_id,
                RepositoryUsage.period_start == run.usage_period_start,
            )
            .values(
                reserved_tokens=case(
                    (
                        RepositoryUsage.reserved_tokens >= reserved,
                        RepositoryUsage.reserved_tokens - reserved,
                    ),
                    else_=0,
                ),
                updated_at=current,
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise LookupError("Usage period does not exist")
        run.usage_reservation_tokens = 0
        run.usage_settled_at = current
        self.session.flush()

    def snapshot(
        self,
        repository_id: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> UsageSnapshot:
        period = monthly_period_start(now)
        usage = self.session.scalar(
            select(RepositoryUsage).where(
                RepositoryUsage.repository_id == repository_id,
                RepositoryUsage.period_start == period,
            )
        )
        return UsageSnapshot(
            period_start=period,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            reserved_tokens=usage.reserved_tokens if usage else 0,
            model_calls=usage.model_calls if usage else 0,
            review_runs=usage.review_runs if usage else 0,
            token_budget=token_budget,
        )

    def _ensure_period(self, repository_id: int, period: date, now: datetime) -> None:
        values = {
            "repository_id": repository_id,
            "period_start": period,
            "reserved_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "model_calls": 0,
            "review_runs": 0,
            "created_at": now,
            "updated_at": now,
        }
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect == "postgresql":
            statement = postgresql_insert(RepositoryUsage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["repository_id", "period_start"]
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(RepositoryUsage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["repository_id", "period_start"]
            )
        else:
            raise RuntimeError(f"Unsupported usage budget database dialect: {dialect}")
        self.session.execute(statement)

    def _run(self, review_run_id: int) -> ReviewRun:
        run = self.session.get(ReviewRun, review_run_id)
        if run is None:
            raise LookupError(f"Review run {review_run_id} does not exist")
        return run
