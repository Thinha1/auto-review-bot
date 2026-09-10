"""SQLAlchemy models for the MVP persistence schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.states import NotificationStatus, ReviewRunStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GitHubInstallation(TimestampMixin, Base):
    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    account_login: Mapped[str | None] = mapped_column(String(255))
    account_type: Mapped[str | None] = mapped_column(String(50))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_repository_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    github_installation_id: Mapped[int] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"), index=True
    )
    owner: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    installation: Mapped[GitHubInstallation] = relationship(back_populates="repositories")
    config: Mapped[ReviewConfig | None] = relationship(
        back_populates="repository", cascade="all, delete-orphan", uselist=False
    )
    review_runs: Mapped[list[ReviewRun]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class ReviewConfig(TimestampMixin, Base):
    __tablename__ = "review_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), unique=True
    )
    discord_webhook_encrypted: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    model: Mapped[str] = mapped_column(String(100), default="gpt-5-mini", nullable=False)
    minimum_severity: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    ignored_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    trigger_events: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["opened", "reopened", "synchronize"], nullable=False
    )
    ignore_drafts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    custom_instructions: Mapped[str | None] = mapped_column(Text)
    max_files: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_diff_lines: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    max_findings: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, default=50_000, nullable=False)
    max_model_calls: Mapped[int] = mapped_column(Integer, default=20, nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="config")


class WebhookDelivery(TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_delivery_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    event: Mapped[str] = mapped_column(String(100))
    action: Mapped[str | None] = mapped_column(String(100))
    pull_number: Mapped[int | None] = mapped_column(Integer)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    github_installation_id: Mapped[int | None] = mapped_column(BigInteger)


class ReviewRun(TimestampMixin, Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "pull_number", "head_sha", name="uq_review_run_idempotency"
        ),
        Index("ix_review_runs_queue_ready", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    pull_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(64))
    base_sha: Mapped[str | None] = mapped_column(String(64))
    trigger: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), default=ReviewRunStatus.QUEUED.value, nullable=False, index=True
    )
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    risk: Mapped[str | None] = mapped_column(String(20))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(100))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    skipped_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_lines: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="review_runs")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan"
    )
    notification_deliveries: Mapped[list[NotificationDelivery]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan"
    )


class Finding(TimestampMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("review_run_id", "fingerprint", name="uq_finding_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float)
    file: Mapped[str] = mapped_column(Text)
    line: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    explanation: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    review_run: Mapped[ReviewRun] = relationship(back_populates="findings")


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=NotificationStatus.PENDING.value, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review_run: Mapped[ReviewRun] = relationship(back_populates="notification_deliveries")


class DashboardSession(TimestampMixin, Base):
    __tablename__ = "dashboard_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    github_login: Mapped[str] = mapped_column(String(255), index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    csrf_token: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
