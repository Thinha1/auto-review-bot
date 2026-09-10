"""SQLAlchemy implementations of application persistence ports."""

from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.states import ReviewRunStatus, validate_review_run_transition
from app.storage.models import Repository, ReviewRun, WebhookDelivery


class RepositoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_github_id(self, github_repository_id: int) -> Repository | None:
        return self.session.scalar(
            select(Repository).where(Repository.github_repository_id == github_repository_id)
        )

    def add(self, repository: Repository) -> Repository:
        self.session.add(repository)
        self.session.flush()
        return repository


class WebhookDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_github_delivery_id(self, github_delivery_id: str) -> WebhookDelivery | None:
        return self.session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.github_delivery_id == github_delivery_id)
        )

    def add(self, delivery: WebhookDelivery) -> WebhookDelivery:
        self.session.add(delivery)
        self.session.flush()
        return delivery

    def record_if_new(self, **values: object) -> tuple[WebhookDelivery, bool]:
        delivery_id = str(values["github_delivery_id"])
        existing = self.get_by_github_delivery_id(delivery_id)
        if existing is not None:
            return existing, False
        delivery = WebhookDelivery(**values)  # type: ignore[arg-type]
        try:
            with self.session.begin_nested():
                self.session.add(delivery)
                self.session.flush()
        except IntegrityError:
            existing = self.get_by_github_delivery_id(delivery_id)
            if existing is None:
                raise
            return existing, False
        return delivery, True


class ReviewRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, review_run_id: int) -> ReviewRun | None:
        return self.session.get(ReviewRun, review_run_id)

    def get_by_idempotency(
        self, repository_id: int, pull_number: int, head_sha: str
    ) -> ReviewRun | None:
        return self.session.scalar(
            select(ReviewRun).where(
                ReviewRun.repository_id == repository_id,
                ReviewRun.pull_number == pull_number,
                ReviewRun.head_sha == head_sha,
            )
        )

    def create_if_new(self, **values: object) -> tuple[ReviewRun, bool]:
        key = (
            cast(int, values["repository_id"]),
            cast(int, values["pull_number"]),
            cast(str, values["head_sha"]),
        )
        existing = self.get_by_idempotency(*key)
        if existing is not None:
            return existing, False
        review_run = ReviewRun(**values)  # type: ignore[arg-type]
        try:
            with self.session.begin_nested():
                self.session.add(review_run)
                self.session.flush()
        except IntegrityError:
            existing = self.get_by_idempotency(*key)
            if existing is None:
                raise
            return existing, False
        return review_run, True

    def set_status(self, review_run_id: int, target: ReviewRunStatus) -> ReviewRun:
        review_run = self.get(review_run_id)
        if review_run is None:
            raise LookupError(f"Review run {review_run_id} does not exist")
        validate_review_run_transition(ReviewRunStatus(review_run.status), target)
        review_run.status = target.value
        self.session.flush()
        return review_run
