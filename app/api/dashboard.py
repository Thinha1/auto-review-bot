"""Server-rendered dashboard with GitHub OAuth, authorization, and CSRF protection."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from app.application.usage import UsageService
from app.authz import RepositoryAction, RepositoryRole, role_allows
from app.domain.states import ReviewRunStatus, validate_review_run_transition
from app.github.oauth import GitHubOAuthClient
from app.notifications.discord import DiscordNotifier, DiscordReview
from app.review.schemas import FindingSchema, ReviewResult, Severity
from app.security import SecretCipher, validate_discord_webhook_url
from app.storage.database import session_scope
from app.storage.models import (
    DashboardSession,
    NotificationDelivery,
    Repository,
    ReviewConfig,
    ReviewRun,
)
from app.storage.usage import SQLAlchemyUsageBudgetStore

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parents[2] / "templates"))
SESSION_COOKIE = "pr_review_session"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _oauth_client(request: Request) -> GitHubOAuthClient:
    client = getattr(request.app.state, "oauth_client", None)
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub OAuth is not configured")
    return client


def _cipher(request: Request) -> SecretCipher:
    return SecretCipher(request.app.state.settings.master_key)


def _current_session(request: Request, database: Any) -> DashboardSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    dashboard_session = database.scalar(
        select(DashboardSession).where(DashboardSession.token_hash == _hash_token(token))
    )
    if dashboard_session is None:
        return None
    expires_at = dashboard_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        database.delete(dashboard_session)
        return None
    return dashboard_session


def _require_csrf(request: Request, dashboard_session: DashboardSession, token: str) -> None:
    if not hmac.compare_digest(dashboard_session.csrf_token, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")


def _repository_role(
    request: Request, dashboard_session: DashboardSession, repository: Repository
) -> RepositoryRole | None:
    access_token = _cipher(request).decrypt(dashboard_session.access_token_encrypted)
    return _oauth_client(request).repository_role(access_token, repository.owner, repository.name)


def _require_repository_action(
    request: Request,
    dashboard_session: DashboardSession,
    repository: Repository,
    action: RepositoryAction,
) -> RepositoryRole:
    role = _repository_role(request, dashboard_session, repository)
    if role is None or not role_allows(role, action):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Repository {action.value} access required",
        )
    return role


@router.get("/login/github")
def login_github(request: Request) -> RedirectResponse:
    nonce = secrets.token_urlsafe(24)
    serializer = URLSafeTimedSerializer(
        request.app.state.settings.session_secret or request.app.state.settings.master_key,
        salt="github-oauth-state",
    )
    state_token = serializer.dumps({"nonce": nonce})
    redirect_uri = f"{request.app.state.settings.public_base_url.rstrip('/')}/auth/github/callback"
    return RedirectResponse(_oauth_client(request).authorization_url(redirect_uri, state_token))


@router.get("/auth/github/callback")
def github_callback(request: Request, code: str, state: str) -> RedirectResponse:
    serializer = URLSafeTimedSerializer(
        request.app.state.settings.session_secret or request.app.state.settings.master_key,
        salt="github-oauth-state",
    )
    try:
        serializer.loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state") from exc
    redirect_uri = f"{request.app.state.settings.public_base_url.rstrip('/')}/auth/github/callback"
    oauth = _oauth_client(request)
    access_token = oauth.exchange_code(code, redirect_uri)
    login = oauth.get_login(access_token)
    cookie_token = secrets.token_urlsafe(32)
    with session_scope(request.app.state.session_factory) as database:
        database.add(
            DashboardSession(
                token_hash=_hash_token(cookie_token),
                github_login=login,
                access_token_encrypted=_cipher(request).encrypt(access_token),
                csrf_token=secrets.token_urlsafe(24),
                expires_at=datetime.now(UTC) + timedelta(hours=12),
            )
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        cookie_token,
        httponly=True,
        secure=request.app.state.settings.public_base_url.startswith("https://"),
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    with session_scope(request.app.state.session_factory) as database:
        dashboard_session = _current_session(request, database)
        if dashboard_session is None:
            return RedirectResponse("/login/github", status_code=status.HTTP_303_SEE_OTHER)
        access_token = _cipher(request).decrypt(dashboard_session.access_token_encrypted)
        rows = []
        for repository in database.scalars(
            select(Repository).order_by(Repository.owner, Repository.name)
        ):
            role = _oauth_client(request).repository_role(
                access_token, repository.owner, repository.name
            )
            if role is None:
                continue
            rows.append(
                {
                    "repository": repository,
                    "config": repository.config,
                    "role": role,
                    "usage": UsageService(SQLAlchemyUsageBudgetStore(database)).snapshot(
                        repository.id,
                        token_budget=(
                            repository.config.monthly_token_budget
                            if repository.config is not None
                            else 1_000_000
                        ),
                    ),
                    "latest": database.scalar(
                        select(ReviewRun)
                        .where(ReviewRun.repository_id == repository.id)
                        .order_by(ReviewRun.created_at.desc())
                        .limit(1)
                    ),
                }
            )
        return templates.TemplateResponse(
            request,
            "repositories.html",
            {
                "rows": rows,
                "user": dashboard_session.github_login,
                "github_app_slug": request.app.state.settings.github_app_slug,
            },
        )


@router.get("/dashboard/repositories/{repository_id}", response_class=HTMLResponse)
def repository_settings(request: Request, repository_id: int) -> Response:
    with session_scope(request.app.state.session_factory) as database:
        dashboard_session = _current_session(request, database)
        if dashboard_session is None:
            return RedirectResponse("/login/github", status_code=status.HTTP_303_SEE_OTHER)
        repository = database.get(Repository, repository_id)
        if repository is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
        role = _require_repository_action(
            request, dashboard_session, repository, RepositoryAction.VIEW
        )
        runs = list(
            database.scalars(
                select(ReviewRun)
                .where(ReviewRun.repository_id == repository.id)
                .order_by(ReviewRun.created_at.desc())
                .limit(100)
            )
        )
        config = repository.config
        usage = UsageService(SQLAlchemyUsageBudgetStore(database)).snapshot(
            repository.id,
            token_budget=config.monthly_token_budget if config is not None else 1_000_000,
        )
        return templates.TemplateResponse(
            request,
            "repository.html",
            {
                "repository": repository,
                "config": config,
                "runs": runs,
                "usage": usage,
                "csrf_token": dashboard_session.csrf_token,
                "role": role,
                "can_configure": role_allows(role, RepositoryAction.CONFIGURE),
                "can_operate": role_allows(role, RepositoryAction.OPERATE),
            },
        )


@router.post("/dashboard/repositories/{repository_id}")
async def update_repository_settings(request: Request, repository_id: int) -> RedirectResponse:
    form = await request.form()
    with session_scope(request.app.state.session_factory) as database:
        dashboard_session = _current_session(request, database)
        if dashboard_session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
        repository = database.get(Repository, repository_id)
        if repository is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
        _require_repository_action(
            request, dashboard_session, repository, RepositoryAction.CONFIGURE
        )
        _require_csrf(request, dashboard_session, str(form.get("csrf_token", "")))
        config = repository.config or ReviewConfig(repository=repository)
        repository.enabled = form.get("enabled") == "on"
        config.ignore_drafts = form.get("ignore_drafts") == "on"
        config.trigger_events = [
            event
            for event in ("opened", "reopened", "synchronize", "ready_for_review")
            if form.get(f"trigger_{event}") == "on"
        ]
        config.model = str(form.get("model", "gpt-5-mini"))[:100]
        severity = str(form.get("minimum_severity", "medium"))
        if severity not in {item.value for item in Severity}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid severity")
        config.minimum_severity = severity
        config.ignored_paths = [
            line.strip() for line in str(form.get("ignored_paths", "")).splitlines() if line.strip()
        ]
        config.custom_instructions = str(form.get("custom_instructions", ""))[:10_000] or None
        for field, default, minimum, maximum in (
            ("max_files", 100, 1, 3000),
            ("max_diff_lines", 5000, 1, 100_000),
            ("max_findings", 20, 1, 100),
            ("max_input_tokens", 50_000, 1, 1_000_000),
            ("max_model_calls", 20, 1, 100),
            ("max_output_tokens_per_call", 4000, 100, 100_000),
            ("monthly_token_budget", 1_000_000, 1000, 1_000_000_000),
        ):
            raw_value = form.get(field, default)
            if not isinstance(raw_value, (str, int)):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid {field}")
            try:
                value = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid {field}"
                ) from exc
            setattr(config, field, max(minimum, min(value, maximum)))
        webhook_url = str(form.get("discord_webhook", "")).strip()
        if form.get("clear_discord_webhook") == "on":
            config.discord_webhook_encrypted = None
        elif webhook_url:
            validate_discord_webhook_url(webhook_url)
            config.discord_webhook_encrypted = _cipher(request).encrypt(webhook_url)
        database.add(config)
    return RedirectResponse(
        f"/dashboard/repositories/{repository_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/dashboard/runs/{review_run_id}", response_class=HTMLResponse)
def review_run_detail(request: Request, review_run_id: int) -> Response:
    with session_scope(request.app.state.session_factory) as database:
        dashboard_session = _current_session(request, database)
        run = database.get(ReviewRun, review_run_id)
        if dashboard_session is None:
            return RedirectResponse("/login/github", status_code=status.HTTP_303_SEE_OTHER)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Review run not found")
        role = _require_repository_action(
            request, dashboard_session, run.repository, RepositoryAction.VIEW
        )
        return templates.TemplateResponse(
            request,
            "run.html",
            {
                "run": run,
                "repository": run.repository,
                "csrf_token": dashboard_session.csrf_token,
                "role": role,
            },
        )


@router.post("/dashboard/runs/{review_run_id}/retry")
async def retry_review(request: Request, review_run_id: int) -> RedirectResponse:
    form = await request.form()
    with session_scope(request.app.state.session_factory) as database:
        dashboard_session = _current_session(request, database)
        run = database.get(ReviewRun, review_run_id)
        if dashboard_session is None or run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Review run not found")
        _require_repository_action(
            request, dashboard_session, run.repository, RepositoryAction.OPERATE
        )
        _require_csrf(request, dashboard_session, str(form.get("csrf_token", "")))
        validate_review_run_transition(ReviewRunStatus(run.status), ReviewRunStatus.QUEUED)
        run.status = ReviewRunStatus.QUEUED.value
        run.attempt_count = 0
        run.next_attempt_at = None
        run.failure_code = None
        run.error = None
        run.last_error = None
        run.completed_at = None
        run.usage_period_start = None
        run.usage_reservation_tokens = 0
        run.usage_settled_at = None
    return RedirectResponse(
        f"/dashboard/repositories/{run.repository_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/dashboard/runs/{review_run_id}/resend")
async def resend_notification(request: Request, review_run_id: int) -> RedirectResponse:
    form = await request.form()
    with session_scope(request.app.state.session_factory) as database:
        dashboard_session = _current_session(request, database)
        run = database.get(ReviewRun, review_run_id)
        if dashboard_session is None or run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Review run not found")
        _require_repository_action(
            request, dashboard_session, run.repository, RepositoryAction.OPERATE
        )
        _require_csrf(request, dashboard_session, str(form.get("csrf_token", "")))
        config = run.repository.config
        if config is None or not config.discord_webhook_encrypted:
            raise HTTPException(status.HTTP_409_CONFLICT, "Discord webhook is not configured")
        result = ReviewResult(
            summary=run.summary or "Review completed.",
            risk=Severity(run.risk or "low"),
            findings=[
                FindingSchema(
                    severity=Severity(item.severity),
                    file=item.file,
                    line=item.line,
                    title=item.title,
                    explanation=item.explanation,
                    suggestion=item.suggestion,
                    confidence=item.confidence or 0,
                )
                for item in run.findings
            ],
            input_tokens=run.input_tokens or 0,
            output_tokens=run.output_tokens or 0,
            is_partial=run.is_partial,
            skipped_files=run.skipped_files,
            skipped_lines=run.skipped_lines,
        )
        review = DiscordReview(
            repository=f"{run.repository.owner}/{run.repository.name}",
            pull_number=run.pull_number,
            title="Review result",
            author="GitHub",
            url=(
                f"https://github.com/{run.repository.owner}/{run.repository.name}/pull/"
                f"{run.pull_number}"
            ),
            result=result,
        )
        encrypted_webhook = config.discord_webhook_encrypted

    notifier = getattr(request.app.state, "discord_notifier", None)
    if notifier is None:
        notifier = DiscordNotifier()
    try:
        message_ids = notifier.send(_cipher(request).decrypt(encrypted_webhook), review)
        delivery_status = "sent"
        last_error = None
    except Exception as exc:
        message_ids = []
        delivery_status = "failed"
        last_error = type(exc).__name__
    with session_scope(request.app.state.session_factory) as database:
        delivery = NotificationDelivery(
            review_run_id=review_run_id,
            status=delivery_status,
            attempts=1,
            message_ids=message_ids,
            last_error=last_error,
            sent_at=datetime.now(UTC) if delivery_status == "sent" else None,
        )
        database.add(delivery)
    return RedirectResponse(
        f"/dashboard/repositories/{run.repository_id}", status_code=status.HTTP_303_SEE_OTHER
    )
