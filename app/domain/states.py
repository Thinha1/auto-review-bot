"""State machines shared by the API, worker, and dashboard."""

from enum import StrEnum


class InvalidStateTransitionError(ValueError):
    """Raised when a persisted entity is moved to an invalid state."""


class ReviewRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    SUPERSEDED = "superseded"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


REVIEW_RUN_TRANSITIONS: dict[ReviewRunStatus, frozenset[ReviewRunStatus]] = {
    ReviewRunStatus.QUEUED: frozenset(
        {ReviewRunStatus.RUNNING, ReviewRunStatus.SKIPPED, ReviewRunStatus.SUPERSEDED}
    ),
    ReviewRunStatus.RUNNING: frozenset(
        {
            ReviewRunStatus.COMPLETED,
            ReviewRunStatus.FAILED,
            ReviewRunStatus.QUEUED,
            ReviewRunStatus.SKIPPED,
            ReviewRunStatus.SUPERSEDED,
        }
    ),
    ReviewRunStatus.COMPLETED: frozenset(),
    # A failed run may only be re-queued by an explicit operator action. Automatic
    # retries happen while RUNNING and never pass through FAILED.
    ReviewRunStatus.FAILED: frozenset({ReviewRunStatus.QUEUED}),
    ReviewRunStatus.SKIPPED: frozenset(),
    ReviewRunStatus.SUPERSEDED: frozenset(),
}

NOTIFICATION_TRANSITIONS: dict[NotificationStatus, frozenset[NotificationStatus]] = {
    NotificationStatus.PENDING: frozenset({NotificationStatus.SENT, NotificationStatus.FAILED}),
    NotificationStatus.SENT: frozenset(),
    NotificationStatus.FAILED: frozenset({NotificationStatus.PENDING}),
}


def validate_review_run_transition(current: ReviewRunStatus, target: ReviewRunStatus) -> None:
    if target not in REVIEW_RUN_TRANSITIONS[current]:
        raise InvalidStateTransitionError(f"Cannot transition from {current} to {target}")


def validate_notification_transition(
    current: NotificationStatus, target: NotificationStatus
) -> None:
    if target not in NOTIFICATION_TRANSITIONS[current]:
        raise InvalidStateTransitionError(f"Cannot transition from {current} to {target}")
