"""GitHub webhook HTTP boundary."""

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.application.webhooks import GitHubWebhookService
from app.metrics import metrics
from app.storage.database import session_scope

router = APIRouter()
MAX_WEBHOOK_BYTES = 1_000_000


def verify_webhook_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/api/webhooks/github")
async def github_webhook(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/json":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Expected application/json")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large")
    settings = request.app.state.settings
    if not verify_webhook_signature(
        body, request.headers.get("X-Hub-Signature-256"), settings.github_webhook_secret
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid webhook signature")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    event = request.headers.get("X-GitHub-Event")
    if not delivery_id or not event:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing GitHub delivery headers")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from exc

    with session_scope(request.app.state.session_factory) as session:
        outcome = GitHubWebhookService(session).handle(delivery_id, event, payload)
    metrics.increment("pr_review_webhooks_total")
    if outcome.duplicate:
        metrics.increment("pr_review_webhook_duplicates_total")
    return JSONResponse(
        {
            "accepted": outcome.accepted,
            "duplicate": outcome.duplicate,
            "review_run_id": outcome.review_run_id,
            "reason": outcome.reason,
        },
        status_code=status.HTTP_202_ACCEPTED,
    )
