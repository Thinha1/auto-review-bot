from datetime import UTC, datetime

from app.application.review_eligibility import review_ineligibility_reason


def test_suspended_installation_takes_precedence_over_repository_state() -> None:
    assert (
        review_ineligibility_reason(
            repository_enabled=False,
            installation_suspended_at=datetime(2026, 9, 11, tzinfo=UTC),
        )
        == "installation_suspended"
    )


def test_disabled_repository_is_ineligible() -> None:
    assert (
        review_ineligibility_reason(
            repository_enabled=False,
            installation_suspended_at=None,
        )
        == "repository_disabled"
    )


def test_enabled_repository_with_active_installation_is_eligible() -> None:
    assert (
        review_ineligibility_reason(
            repository_enabled=True,
            installation_suspended_at=None,
        )
        is None
    )
