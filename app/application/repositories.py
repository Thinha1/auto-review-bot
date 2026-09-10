"""Persistence ports used by application services."""

from typing import Protocol

from app.domain.states import ReviewRunStatus
from app.storage.models import Repository, ReviewRun, WebhookDelivery


class RepositoryRepository(Protocol):
    def get_by_github_id(self, github_repository_id: int) -> Repository | None: ...

    def add(self, repository: Repository) -> Repository: ...


class WebhookDeliveryRepository(Protocol):
    def get_by_github_delivery_id(self, github_delivery_id: str) -> WebhookDelivery | None: ...

    def add(self, delivery: WebhookDelivery) -> WebhookDelivery: ...

    def record_if_new(self, **values: object) -> tuple[WebhookDelivery, bool]: ...


class ReviewRunRepository(Protocol):
    def get(self, review_run_id: int) -> ReviewRun | None: ...

    def get_by_idempotency(
        self, repository_id: int, pull_number: int, head_sha: str
    ) -> ReviewRun | None: ...

    def create_if_new(self, **values: object) -> tuple[ReviewRun, bool]: ...

    def set_status(self, review_run_id: int, target: ReviewRunStatus) -> ReviewRun: ...
