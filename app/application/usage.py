"""Application service for repository token budgets and usage reporting."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    period_start: date
    input_tokens: int
    output_tokens: int
    reserved_tokens: int
    model_calls: int
    review_runs: int
    token_budget: int

    @property
    def used_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.token_budget - self.used_tokens - self.reserved_tokens)


def monthly_period_start(now: datetime | None = None) -> date:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).date().replace(day=1)


class UsageBudgetStore(Protocol):
    def reserve(
        self,
        review_run_id: int,
        tokens: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> bool: ...

    def settle(
        self,
        review_run_id: int,
        *,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        now: datetime | None = None,
    ) -> None: ...

    def release(self, review_run_id: int, *, now: datetime | None = None) -> None: ...

    def snapshot(
        self,
        repository_id: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> UsageSnapshot: ...


class UsageService:
    def __init__(self, store: UsageBudgetStore) -> None:
        self.store = store

    def reserve(
        self,
        review_run_id: int,
        tokens: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> bool:
        if tokens < 0 or token_budget < 0:
            raise ValueError("Token reservation and budget must be non-negative")
        return self.store.reserve(
            review_run_id,
            tokens,
            token_budget=token_budget,
            now=now,
        )

    def settle(
        self,
        review_run_id: int,
        *,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        now: datetime | None = None,
    ) -> None:
        if min(input_tokens, output_tokens, model_calls) < 0:
            raise ValueError("Usage counters must be non-negative")
        self.store.settle(
            review_run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_calls=model_calls,
            now=now,
        )

    def release(self, review_run_id: int, *, now: datetime | None = None) -> None:
        self.store.release(review_run_id, now=now)

    def snapshot(
        self,
        repository_id: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> UsageSnapshot:
        if token_budget < 0:
            raise ValueError("Token budget must be non-negative")
        return self.store.snapshot(repository_id, token_budget=token_budget, now=now)
