"""Database-backed review worker and process entry point."""

import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, Thread
from time import monotonic, sleep
from typing import Protocol

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.application.usage import UsageService
from app.config import Settings, get_settings
from app.domain.states import NotificationStatus, ReviewRunStatus
from app.github.auth import GitHubAppAuth
from app.github.client import GitHubClient, PublishedCheck
from app.github.schemas import PullRequestData
from app.metrics import Metrics, MetricsServer, metrics
from app.models.base import ModelOutputError, ModelProvider
from app.models.openai import create_openai_provider
from app.notifications.discord import DiscordNotifier, DiscordReview
from app.review.diff import parse_file_patch
from app.review.engine import ReviewEngine, finding_fingerprint
from app.review.filters import prepare_diff
from app.review.prompts import PROMPT_VERSION
from app.review.schemas import ReviewResult, Severity
from app.review.usage import estimate_token_reservation
from app.security import SecretCipher
from app.storage.database import create_engine, make_session_factory, session_scope
from app.storage.models import Finding, GitHubCheckDelivery, NotificationDelivery, ReviewRun
from app.storage.queue import ReviewQueue
from app.storage.usage import SQLAlchemyUsageBudgetStore

logger = logging.getLogger(__name__)


def is_transient_failure(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, ModelOutputError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def elapsed_seconds(start: datetime, end: datetime) -> float:
    """Return a non-negative duration for SQLite-naive or timezone-aware timestamps."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0.0, (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds())


class PullRequestClient(Protocol):
    def get_pull(self, owner: str, name: str, pull_number: int) -> PullRequestData: ...

    def get_head_sha(self, owner: str, name: str, pull_number: int) -> str: ...

    def publish_review_check(
        self,
        owner: str,
        name: str,
        head_sha: str,
        details_url: str,
        result: ReviewResult,
    ) -> PublishedCheck: ...


class NotificationSender(Protocol):
    def send(self, webhook_url: str, review: DiscordReview) -> list[str]: ...


class LeaseKeeper:
    """Extend a worker lease while slow external calls are in flight."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        review_run_id: int,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self.factory = factory
        self.review_run_id = review_run_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.stop = Event()
        self.thread = Thread(target=self._run, daemon=True)

    def __enter__(self) -> "LeaseKeeper":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        interval = max(1.0, min(self.lease_seconds / 3, 30.0))
        while not self.stop.wait(interval):
            try:
                with session_scope(self.factory) as session:
                    owned = ReviewQueue(session).heartbeat(
                        self.review_run_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                if not owned:
                    return
            except Exception:
                logger.error(
                    "Lease heartbeat failed",
                    extra={"review_run_id": self.review_run_id},
                )
                return


@dataclass(frozen=True, slots=True)
class RunContext:
    id: int
    repository: str
    owner: str
    name: str
    installation_id: int
    pull_number: int
    head_sha: str
    model: str
    minimum_severity: str
    ignored_paths: list[str]
    custom_instructions: str | None
    max_files: int
    max_diff_lines: int
    max_findings: int
    max_input_tokens: int
    max_model_calls: int
    max_output_tokens_per_call: int
    monthly_token_budget: int
    discord_webhook_encrypted: str | None


class ReviewProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        github_factory: Callable[[int], PullRequestClient],
        model_provider: ModelProvider,
        notifier: NotificationSender,
        cipher: SecretCipher,
        *,
        worker_id: str,
        lease_seconds: int = 300,
        max_attempts: int = 3,
        poll_seconds: float = 2,
        github_checks_enabled: bool = False,
        metric_registry: Metrics | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.github_factory = github_factory
        self.engine = ReviewEngine(model_provider)
        self.notifier = notifier
        self.cipher = cipher
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.poll_seconds = poll_seconds
        self.github_checks_enabled = github_checks_enabled
        self.metrics = metric_registry if metric_registry is not None else metrics

    def run_once(self) -> bool:
        with session_scope(self.session_factory) as session:
            claimed = ReviewQueue(session).claim_next(
                self.worker_id, lease_seconds=self.lease_seconds
            )
            review_run_id = claimed.id if claimed is not None else None
            queue_latency = (
                elapsed_seconds(
                    claimed.next_attempt_at or claimed.created_at,
                    claimed.started_at,
                )
                if claimed is not None and claimed.started_at is not None
                else None
            )
        if review_run_id is None:
            return False
        self.metrics.increment("pr_review_runs_claimed_total")
        if queue_latency is not None:
            self.metrics.observe("pr_review_queue_latency_seconds", queue_latency)
        processing_started = monotonic()
        try:
            with LeaseKeeper(
                self.session_factory, review_run_id, self.worker_id, self.lease_seconds
            ):
                self._process(review_run_id)
        except Exception as exc:
            # Error strings may contain credentials or source. Persist only a stable class name.
            failure_code = type(exc).__name__
            logger.error(
                "Review run failed",
                extra={"review_run_id": review_run_id, "error_type": failure_code},
            )
            with session_scope(self.session_factory) as session:
                run = ReviewQueue(session).retry_or_fail(
                    review_run_id,
                    self.worker_id,
                    failure_code=failure_code,
                    max_attempts=self.max_attempts if is_transient_failure(exc) else 1,
                )
                if run.status == ReviewRunStatus.FAILED.value:
                    UsageService(SQLAlchemyUsageBudgetStore(session)).release(review_run_id)
                    self.metrics.increment("pr_review_runs_failed_total")
                else:
                    self.metrics.increment("pr_review_runs_retried_total")
            self.metrics.increment("pr_review_runs_failed_attempts_total")
        finally:
            self.metrics.observe("pr_review_duration_seconds", monotonic() - processing_started)
        return True

    def _load_context(self, review_run_id: int) -> RunContext:
        with session_scope(self.session_factory) as session:
            run = session.get(ReviewRun, review_run_id)
            if (
                run is None
                or run.status != ReviewRunStatus.RUNNING.value
                or run.locked_by != self.worker_id
            ):
                raise LookupError("Worker does not own review run")
            repository = run.repository
            config = repository.config
            if config is None:
                raise ValueError("Repository review configuration is missing")
            snapshot = run.config_snapshot or {}
            return RunContext(
                id=run.id,
                repository=f"{repository.owner}/{repository.name}",
                owner=repository.owner,
                name=repository.name,
                installation_id=repository.installation.github_installation_id,
                pull_number=run.pull_number,
                head_sha=run.head_sha,
                model=str(snapshot.get("model", config.model)),
                minimum_severity=str(snapshot.get("minimum_severity", config.minimum_severity)),
                ignored_paths=list(snapshot.get("ignored_paths", config.ignored_paths)),
                custom_instructions=snapshot.get("custom_instructions", config.custom_instructions),
                max_files=int(snapshot.get("max_files", config.max_files)),
                max_diff_lines=int(snapshot.get("max_diff_lines", config.max_diff_lines)),
                max_findings=int(snapshot.get("max_findings", config.max_findings)),
                max_input_tokens=int(snapshot.get("max_input_tokens", config.max_input_tokens)),
                max_model_calls=int(snapshot.get("max_model_calls", config.max_model_calls)),
                max_output_tokens_per_call=int(
                    snapshot.get("max_output_tokens_per_call", config.max_output_tokens_per_call)
                ),
                monthly_token_budget=int(
                    snapshot.get("monthly_token_budget", config.monthly_token_budget)
                ),
                discord_webhook_encrypted=config.discord_webhook_encrypted,
            )

    def _process(self, review_run_id: int) -> None:
        started = monotonic()
        context = self._load_context(review_run_id)
        github = self.github_factory(context.installation_id)
        pull = github.get_pull(context.owner, context.name, context.pull_number)
        if pull.head_sha != context.head_sha:
            self._mark_superseded(review_run_id)
            return

        files = [parse_file_patch(file.path, file.patch, file.status) for file in pull.files]
        prepared = prepare_diff(
            files,
            ignored_paths=context.ignored_paths,
            max_files=context.max_files,
            max_diff_lines=context.max_diff_lines,
            max_input_tokens=context.max_input_tokens,
            max_model_calls=context.max_model_calls,
        )
        unavailable = [file for file in pull.files if file.patch is None]
        if unavailable:
            prepared.skipped_lines += sum(file.changes for file in unavailable)
            prepared.is_partial = True
        if pull.files_truncated:
            prepared.is_partial = True

        reservation_tokens = estimate_token_reservation(
            prepared,
            custom_instructions=context.custom_instructions,
            max_output_tokens_per_call=context.max_output_tokens_per_call,
        )
        if not self._reserve_usage(
            review_run_id,
            reservation_tokens,
            token_budget=context.monthly_token_budget,
        ):
            self._mark_budget_exceeded(review_run_id)
            return

        result = self.engine.review(
            prepared,
            model=context.model,
            max_findings=context.max_findings,
            minimum_severity=Severity(context.minimum_severity),
            custom_instructions=context.custom_instructions,
            max_output_tokens_per_call=context.max_output_tokens_per_call,
        )
        self.metrics.increment("pr_review_model_calls_total", len(prepared.chunks))
        self.metrics.increment("pr_review_model_input_tokens_total", result.input_tokens)
        self.metrics.increment("pr_review_model_output_tokens_total", result.output_tokens)
        if (
            github.get_head_sha(context.owner, context.name, context.pull_number)
            != context.head_sha
        ):
            self._mark_superseded(
                review_run_id,
                result=result,
                model_calls=len(prepared.chunks),
            )
            return

        notification_id, check_delivery_id = self._save_result(
            review_run_id,
            result,
            model_calls=len(prepared.chunks),
            duration_ms=int((monotonic() - started) * 1000),
        )
        self.metrics.increment("pr_review_runs_completed_total")
        if check_delivery_id is not None:
            self._publish_github_check(
                check_delivery_id,
                github,
                context,
                pull.url,
                result,
            )
        if notification_id is not None and context.discord_webhook_encrypted is not None:
            self._send_notification(
                notification_id,
                context.discord_webhook_encrypted,
                DiscordReview(
                    repository=context.repository,
                    pull_number=context.pull_number,
                    title=pull.title,
                    author=pull.author,
                    url=pull.url,
                    result=result,
                ),
            )

    def _save_result(
        self,
        review_run_id: int,
        result: ReviewResult,
        *,
        model_calls: int,
        duration_ms: int,
    ) -> tuple[int | None, int | None]:
        with session_scope(self.session_factory) as session:
            run = ReviewQueue(session)._locked_run(review_run_id, self.worker_id)
            UsageService(SQLAlchemyUsageBudgetStore(session)).settle(
                review_run_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_calls=model_calls,
            )
            run.summary = result.summary
            run.risk = result.risk.value
            run.input_tokens = result.input_tokens
            run.output_tokens = result.output_tokens
            run.model_calls = model_calls
            run.is_partial = result.is_partial
            run.skipped_files = result.skipped_files
            run.skipped_lines = result.skipped_lines
            run.duration_ms = duration_ms
            run.prompt_version = PROMPT_VERSION
            run.findings = [
                Finding(
                    severity=item.severity.value,
                    confidence=item.confidence,
                    file=item.file,
                    line=item.line,
                    title=item.title,
                    explanation=item.explanation,
                    suggestion=item.suggestion,
                    fingerprint=finding_fingerprint(item),
                )
                for item in result.findings
            ]
            run.status = ReviewRunStatus.COMPLETED.value
            run.completed_at = datetime.now(UTC)
            run.locked_at = None
            run.locked_by = None
            run.lease_expires_at = None
            notification_delivery = None
            check_delivery = None
            if run.repository.config and run.repository.config.discord_webhook_encrypted:
                notification_delivery = NotificationDelivery(review_run=run)
                session.add(notification_delivery)
            if self.github_checks_enabled:
                check_delivery = GitHubCheckDelivery(review_run=run)
                session.add(check_delivery)
            session.flush()
            return (
                notification_delivery.id if notification_delivery is not None else None,
                check_delivery.id if check_delivery is not None else None,
            )

    def _mark_superseded(
        self,
        review_run_id: int,
        *,
        result: ReviewResult | None = None,
        model_calls: int = 0,
    ) -> None:
        with session_scope(self.session_factory) as session:
            run = ReviewQueue(session)._locked_run(review_run_id, self.worker_id)
            if result is not None:
                UsageService(SQLAlchemyUsageBudgetStore(session)).settle(
                    review_run_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    model_calls=model_calls,
                )
                run.input_tokens = result.input_tokens
                run.output_tokens = result.output_tokens
                run.model_calls = model_calls
            run.status = ReviewRunStatus.SUPERSEDED.value
            run.completed_at = datetime.now(UTC)
            run.failure_code = "head_sha_changed"
            run.locked_at = None
            run.locked_by = None
            run.lease_expires_at = None
        self.metrics.increment("pr_review_runs_superseded_total")

    def _reserve_usage(self, review_run_id: int, tokens: int, *, token_budget: int) -> bool:
        with session_scope(self.session_factory) as session:
            return UsageService(SQLAlchemyUsageBudgetStore(session)).reserve(
                review_run_id,
                tokens,
                token_budget=token_budget,
            )

    def _mark_budget_exceeded(self, review_run_id: int) -> None:
        with session_scope(self.session_factory) as session:
            run = ReviewQueue(session)._locked_run(review_run_id, self.worker_id)
            run.status = ReviewRunStatus.SKIPPED.value
            run.completed_at = datetime.now(UTC)
            run.failure_code = "monthly_token_budget_exceeded"
            run.locked_at = None
            run.locked_by = None
            run.lease_expires_at = None
        self.metrics.increment("pr_review_runs_budget_exceeded_total")

    def _send_notification(
        self, notification_id: int, encrypted_webhook: str, review: DiscordReview
    ) -> None:
        delivery_started = monotonic()
        try:
            webhook_url = self.cipher.decrypt(encrypted_webhook)
            message_ids = self.notifier.send(webhook_url, review)
        except Exception as exc:
            with session_scope(self.session_factory) as session:
                delivery = session.get(NotificationDelivery, notification_id)
                if delivery is not None:
                    delivery.status = NotificationStatus.FAILED.value
                    delivery.attempts += 1
                    delivery.last_error = type(exc).__name__
            self.metrics.increment("pr_review_discord_failures_total")
            return
        finally:
            self.metrics.observe(
                "pr_review_discord_delivery_latency_seconds",
                monotonic() - delivery_started,
            )
        with session_scope(self.session_factory) as session:
            delivery = session.get(NotificationDelivery, notification_id)
            if delivery is not None:
                delivery.status = NotificationStatus.SENT.value
                delivery.attempts += 1
                delivery.message_ids = message_ids
                delivery.sent_at = datetime.now(UTC)
                delivery.review_run.notified_at = delivery.sent_at
        self.metrics.increment("pr_review_discord_deliveries_total")

    def _publish_github_check(
        self,
        delivery_id: int,
        github: PullRequestClient,
        context: RunContext,
        details_url: str,
        result: ReviewResult,
    ) -> None:
        try:
            published = github.publish_review_check(
                context.owner,
                context.name,
                context.head_sha,
                details_url,
                result,
            )
        except Exception as exc:
            error_type = type(exc).__name__
            logger.error(
                "GitHub check publication failed",
                extra={"review_run_id": context.id, "error_type": error_type},
            )
            with session_scope(self.session_factory) as session:
                delivery = session.get(GitHubCheckDelivery, delivery_id)
                if delivery is not None:
                    delivery.status = NotificationStatus.FAILED.value
                    delivery.attempts += 1
                    delivery.last_error = error_type
            self.metrics.increment("pr_review_github_check_failures_total")
            return
        with session_scope(self.session_factory) as session:
            delivery = session.get(GitHubCheckDelivery, delivery_id)
            if delivery is not None:
                delivery.github_check_run_id = published.id
                delivery.status = NotificationStatus.SENT.value
                delivery.conclusion = published.conclusion
                delivery.details_url = published.url
                delivery.attempts += 1
                delivery.published_at = datetime.now(UTC)
        self.metrics.increment("pr_review_github_checks_published_total")


def build_processor(settings: Settings) -> tuple[ReviewProcessor, object]:
    required = {
        "GITHUB_APP_ID": settings.github_app_id,
        "GITHUB_PRIVATE_KEY": settings.github_private_key,
        "OPENAI_API_KEY": settings.openai_api_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Worker settings missing: {', '.join(missing)}")
    engine = create_engine(settings.database_url)
    factory = make_session_factory(engine)
    auth = GitHubAppAuth(
        settings.github_app_id or "",
        settings.github_private_key or "",
        api_url=settings.github_api_url,
    )

    def github_factory(installation_id: int) -> GitHubClient:
        token = auth.installation_token(installation_id)
        return GitHubClient(token, api_url=settings.github_api_url)

    processor = ReviewProcessor(
        factory,
        github_factory,
        create_openai_provider(
            settings.openai_api_key or "",
            base_url=settings.openai_base_url,
            api_style=settings.openai_api_style,
            chat_response_format=settings.openai_chat_response_format,
            chat_token_limit_field=settings.openai_chat_token_limit_field,
        ),
        DiscordNotifier(),
        SecretCipher(settings.master_key),
        worker_id=f"{socket.gethostname()}-{__import__('os').getpid()}",
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.worker_max_attempts,
        poll_seconds=settings.worker_poll_seconds,
        github_checks_enabled=settings.github_checks_enabled,
    )
    return processor, engine


def main() -> None:
    settings = get_settings()
    processor, engine = build_processor(settings)
    metrics_server = MetricsServer(
        metrics,
        settings.worker_metrics_host,
        settings.worker_metrics_port,
    )
    metrics_server.start()
    try:
        while True:
            if not processor.run_once():
                sleep(processor.poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopped")
    finally:
        metrics_server.close()
        engine.dispose()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
