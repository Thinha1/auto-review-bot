from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.application.usage import UsageService, UsageSnapshot, monthly_period_start


class UnusedUsageStore:
    def reserve(
        self,
        review_run_id: int,
        tokens: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> bool:
        raise AssertionError("store should not be called")

    def settle(
        self,
        review_run_id: int,
        *,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        now: datetime | None = None,
    ) -> None:
        raise AssertionError("store should not be called")

    def release(self, review_run_id: int, *, now: datetime | None = None) -> None:
        raise AssertionError("store should not be called")

    def snapshot(
        self,
        repository_id: int,
        *,
        token_budget: int,
        now: datetime | None = None,
    ) -> UsageSnapshot:
        raise AssertionError("store should not be called")


def test_usage_service_rejects_negative_counters_before_storage() -> None:
    service = UsageService(UnusedUsageStore())

    with pytest.raises(ValueError, match="non-negative"):
        service.reserve(1, -1, token_budget=100)
    with pytest.raises(ValueError, match="non-negative"):
        service.settle(1, input_tokens=1, output_tokens=-1, model_calls=1)
    with pytest.raises(ValueError, match="non-negative"):
        service.snapshot(1, token_budget=-1)


def test_monthly_period_start_uses_utc_calendar_month() -> None:
    utc_plus_seven = timezone(timedelta(hours=7))

    assert monthly_period_start(datetime(2026, 10, 1, 0, 30, tzinfo=utc_plus_seven)) == date(
        2026, 9, 1
    )
    assert monthly_period_start(datetime(2026, 10, 1, tzinfo=UTC)) == date(2026, 10, 1)


def test_usage_snapshot_never_reports_negative_remaining_tokens() -> None:
    snapshot = UsageSnapshot(
        period_start=date(2026, 9, 1),
        input_tokens=800,
        output_tokens=300,
        reserved_tokens=50,
        model_calls=2,
        review_runs=1,
        token_budget=1000,
    )

    assert snapshot.used_tokens == 1100
    assert snapshot.remaining_tokens == 0
