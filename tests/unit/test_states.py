import pytest

from app.domain.states import (
    InvalidStateTransitionError,
    NotificationStatus,
    ReviewRunStatus,
    validate_notification_transition,
    validate_review_run_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ReviewRunStatus.QUEUED, ReviewRunStatus.RUNNING),
        (ReviewRunStatus.QUEUED, ReviewRunStatus.SKIPPED),
        (ReviewRunStatus.QUEUED, ReviewRunStatus.SUPERSEDED),
        (ReviewRunStatus.RUNNING, ReviewRunStatus.COMPLETED),
        (ReviewRunStatus.RUNNING, ReviewRunStatus.FAILED),
        (ReviewRunStatus.RUNNING, ReviewRunStatus.QUEUED),
        (ReviewRunStatus.RUNNING, ReviewRunStatus.SKIPPED),
        (ReviewRunStatus.RUNNING, ReviewRunStatus.SUPERSEDED),
        (ReviewRunStatus.FAILED, ReviewRunStatus.QUEUED),
    ],
)
def test_review_run_allowed_transitions(current: ReviewRunStatus, target: ReviewRunStatus) -> None:
    validate_review_run_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ReviewRunStatus.COMPLETED, ReviewRunStatus.RUNNING),
        (ReviewRunStatus.FAILED, ReviewRunStatus.RUNNING),
        (ReviewRunStatus.SKIPPED, ReviewRunStatus.RUNNING),
    ],
)
def test_review_run_rejects_terminal_transitions(
    current: ReviewRunStatus, target: ReviewRunStatus
) -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_review_run_transition(current, target)


def test_notification_retry_transition() -> None:
    validate_notification_transition(NotificationStatus.FAILED, NotificationStatus.PENDING)
    with pytest.raises(InvalidStateTransitionError):
        validate_notification_transition(NotificationStatus.SENT, NotificationStatus.PENDING)
