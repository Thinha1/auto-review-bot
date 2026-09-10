"""SQLite-compatible database queue with leases and bounded retries."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.domain.states import ReviewRunStatus
from app.storage.models import ReviewRun


class ReviewQueue:
    def __init__(self, session: Session) -> None:
        self.session = session

    def reclaim_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        result = self.session.execute(
            update(ReviewRun)
            .where(
                ReviewRun.status == ReviewRunStatus.RUNNING.value,
                ReviewRun.lease_expires_at.is_not(None),
                ReviewRun.lease_expires_at <= now,
            )
            .values(
                status=ReviewRunStatus.QUEUED.value,
                locked_at=None,
                locked_by=None,
                lease_expires_at=None,
                next_attempt_at=now,
            )
        )
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> ReviewRun | None:
        now = now or datetime.now(UTC)
        self.reclaim_expired(now)
        for _ in range(5):
            review_run_id = self.session.scalar(
                select(ReviewRun.id)
                .where(
                    ReviewRun.status == ReviewRunStatus.QUEUED.value,
                    or_(ReviewRun.next_attempt_at.is_(None), ReviewRun.next_attempt_at <= now),
                )
                .order_by(ReviewRun.created_at, ReviewRun.id)
                .limit(1)
            )
            if review_run_id is None:
                return None
            result = self.session.execute(
                update(ReviewRun)
                .where(
                    ReviewRun.id == review_run_id,
                    ReviewRun.status == ReviewRunStatus.QUEUED.value,
                )
                .values(
                    status=ReviewRunStatus.RUNNING.value,
                    locked_at=now,
                    locked_by=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    started_at=now,
                    attempt_count=ReviewRun.attempt_count + 1,
                )
            )
            if result.rowcount == 1:  # type: ignore[attr-defined]
                self.session.flush()
                return self.session.get(ReviewRun, review_run_id)
        return None

    def heartbeat(
        self,
        review_run_id: int,
        worker_id: str,
        *,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        result = self.session.execute(
            update(ReviewRun)
            .where(
                ReviewRun.id == review_run_id,
                ReviewRun.status == ReviewRunStatus.RUNNING.value,
                ReviewRun.locked_by == worker_id,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        )
        return result.rowcount == 1  # type: ignore[attr-defined, no-any-return]

    def retry_or_fail(
        self,
        review_run_id: int,
        worker_id: str,
        *,
        failure_code: str,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> ReviewRun:
        now = now or datetime.now(UTC)
        run = self._locked_run(review_run_id, worker_id)
        run.failure_code = failure_code
        run.last_error = failure_code
        run.locked_at = None
        run.locked_by = None
        run.lease_expires_at = None
        if run.attempt_count >= max_attempts:
            run.status = ReviewRunStatus.FAILED.value
            run.completed_at = now
            run.error = failure_code
        else:
            run.status = ReviewRunStatus.QUEUED.value
            run.next_attempt_at = now + timedelta(seconds=min(2**run.attempt_count, 60))
        self.session.flush()
        return run

    def _locked_run(self, review_run_id: int, worker_id: str) -> ReviewRun:
        run = self.session.get(ReviewRun, review_run_id)
        if run is None or run.status != ReviewRunStatus.RUNNING.value or run.locked_by != worker_id:
            raise LookupError("Worker no longer owns this review run")
        return run
