"""Transactional handling of GitHub webhook payloads."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.states import ReviewRunStatus
from app.storage.models import GitHubInstallation, Repository, ReviewConfig
from app.storage.repositories import ReviewRunRepository, WebhookDeliveryRepository


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    accepted: bool
    duplicate: bool = False
    review_run_id: int | None = None
    reason: str | None = None


class GitHubWebhookService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def handle(
        self, github_delivery_id: str, event: str, payload: dict[str, Any]
    ) -> WebhookOutcome:
        action = str(payload.get("action", "")) or None
        pull = payload.get("pull_request") or {}
        delivery, created = WebhookDeliveryRepository(self.session).record_if_new(
            github_delivery_id=github_delivery_id,
            event=event,
            action=action,
            pull_number=pull.get("number") or payload.get("number"),
            head_sha=(pull.get("head") or {}).get("sha"),
            github_installation_id=(payload.get("installation") or {}).get("id"),
        )
        if not created:
            return WebhookOutcome(accepted=True, duplicate=True, reason="duplicate_delivery")

        if event in {"installation", "installation_repositories"}:
            self._handle_installation_event(event, action, payload)
            return WebhookOutcome(accepted=True)
        if event != "pull_request" or action not in {
            "opened",
            "reopened",
            "synchronize",
            "ready_for_review",
        }:
            return WebhookOutcome(accepted=False, reason="unsupported_event")

        repository = self._upsert_repository(payload)
        config = repository.config
        if config is None:
            config = ReviewConfig(repository=repository)
            self.session.add(config)
            self.session.flush()
        head_sha = str(pull["head"]["sha"])
        base_sha = str(pull["base"]["sha"])
        run, run_created = ReviewRunRepository(self.session).create_if_new(
            repository_id=repository.id,
            pull_number=int(payload["number"]),
            head_sha=head_sha,
            base_sha=base_sha,
            trigger=action,
            model=config.model,
            prompt_version="v1",
            config_snapshot=self._config_snapshot(config),
        )
        if not run_created:
            return WebhookOutcome(
                accepted=True, duplicate=True, review_run_id=run.id, reason="duplicate_run"
            )
        skip_reason: str | None = None
        if not repository.enabled:
            skip_reason = "repository_disabled"
        elif action not in config.trigger_events:
            skip_reason = "trigger_disabled"
        elif bool(pull.get("draft")) and config.ignore_drafts:
            skip_reason = "draft_ignored"
        if skip_reason:
            run.status = ReviewRunStatus.SKIPPED.value
            run.failure_code = skip_reason
            run.completed_at = datetime.now(UTC)
        delivery.pull_number = run.pull_number
        delivery.head_sha = run.head_sha
        self.session.flush()
        return WebhookOutcome(accepted=True, review_run_id=run.id, reason=skip_reason)

    def _installation(self, external_id: int) -> GitHubInstallation:
        installation = self.session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.github_installation_id == external_id
            )
        )
        if installation is None:
            installation = GitHubInstallation(github_installation_id=external_id)
            self.session.add(installation)
            self.session.flush()
        return installation

    def _upsert_repository(self, payload: dict[str, Any]) -> Repository:
        installation_payload = payload.get("installation") or {}
        repository_payload = payload["repository"]
        installation = self._installation(int(installation_payload["id"]))
        repository = self.session.scalar(
            select(Repository).where(
                Repository.github_repository_id == int(repository_payload["id"])
            )
        )
        owner = str((repository_payload.get("owner") or {}).get("login") or "")
        name = str(repository_payload["name"])
        if repository is None:
            repository = Repository(
                github_repository_id=int(repository_payload["id"]),
                installation=installation,
                owner=owner,
                name=name,
            )
            self.session.add(repository)
        else:
            repository.installation = installation
            repository.owner = owner
            repository.name = name
        self.session.flush()
        return repository

    def _handle_installation_event(
        self, event: str, action: str | None, payload: dict[str, Any]
    ) -> None:
        external_id = int(payload["installation"]["id"])
        installation = self._installation(external_id)
        account = payload["installation"].get("account") or {}
        installation.account_login = account.get("login")
        installation.account_type = account.get("type")
        if action in {"deleted", "suspend"}:
            installation.suspended_at = datetime.now(UTC)
            for repository in installation.repositories:
                repository.enabled = False
            return
        if action == "unsuspend":
            installation.suspended_at = None

        if event == "installation":
            added = payload.get("repositories") or []
            removed: list[dict[str, Any]] = []
        else:
            added = payload.get("repositories_added") or []
            removed = payload.get("repositories_removed") or []
        for item in added:
            repository = self.session.scalar(
                select(Repository).where(Repository.github_repository_id == int(item["id"]))
            )
            full_name = str(item.get("full_name") or "")
            owner, _, name = full_name.partition("/")
            if repository is None:
                repository = Repository(
                    github_repository_id=int(item["id"]),
                    installation=installation,
                    owner=owner,
                    name=name or str(item.get("name") or ""),
                )
                self.session.add(repository)
            else:
                repository.installation = installation
                repository.owner = owner or repository.owner
                repository.name = name or str(item.get("name") or repository.name)
                repository.enabled = True
        removed_ids = {int(item["id"]) for item in removed}
        for repository in installation.repositories:
            if repository.github_repository_id in removed_ids:
                repository.enabled = False

    @staticmethod
    def _config_snapshot(config: ReviewConfig) -> dict[str, Any]:
        return {
            "model": config.model,
            "minimum_severity": config.minimum_severity,
            "ignored_paths": config.ignored_paths,
            "max_files": config.max_files,
            "max_diff_lines": config.max_diff_lines,
            "max_findings": config.max_findings,
            "max_input_tokens": config.max_input_tokens,
            "max_model_calls": config.max_model_calls,
            "max_output_tokens_per_call": config.max_output_tokens_per_call,
            "monthly_token_budget": config.monthly_token_budget,
            "custom_instructions": config.custom_instructions,
        }
