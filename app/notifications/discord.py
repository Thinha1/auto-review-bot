"""Discord webhook formatting and delivery."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from random import random
from time import sleep
from typing import Any

import httpx

from app.review.schemas import FindingSchema, ReviewResult, Severity
from app.security import validate_discord_webhook_url

_COLORS = {
    Severity.CRITICAL: 0x992D22,
    Severity.HIGH: 0xE74C3C,
    Severity.MEDIUM: 0xF1C40F,
    Severity.LOW: 0x3498DB,
}


class DiscordDeliveryError(RuntimeError):
    pass


class PermanentDiscordError(DiscordDeliveryError):
    pass


@dataclass(frozen=True, slots=True)
class DiscordReview:
    repository: str
    pull_number: int
    title: str
    author: str
    url: str
    result: ReviewResult


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _finding_embed(finding: FindingSchema) -> dict[str, Any]:
    description = f"{finding.explanation}\n\n**Suggestion:** {finding.suggestion or '—'}"
    return {
        "title": _truncate(f"[{finding.severity.value.upper()}] {finding.title}", 256),
        "description": _truncate(description, 3000),
        "color": _COLORS[finding.severity],
        "footer": {
            "text": _truncate(
                f"{finding.file}:{finding.line} · confidence {finding.confidence:.0%}", 2048
            )
        },
    }


def format_discord_messages(review: DiscordReview) -> list[dict[str, Any]]:
    partial = (
        f"\n⚠ Partial review: skipped {review.result.skipped_files} files and "
        f"{review.result.skipped_lines} lines."
        if review.result.is_partial
        else ""
    )
    overview = {
        "title": _truncate(f"PR #{review.pull_number}: {review.title}", 256),
        "url": review.url,
        "description": _truncate(f"{review.result.summary}{partial}", 3500),
        "color": _COLORS[review.result.risk],
        "fields": [
            {"name": "Repository", "value": _truncate(review.repository, 1024), "inline": True},
            {"name": "Author", "value": _truncate(review.author, 1024), "inline": True},
            {"name": "Risk", "value": review.result.risk.value.upper(), "inline": True},
        ],
    }
    finding_embeds = [_finding_embed(finding) for finding in review.result.findings]
    messages: list[dict[str, Any]] = []
    first_batch = finding_embeds[:9]
    messages.append({"embeds": [overview, *first_batch], "allowed_mentions": {"parse": []}})
    for index in range(9, len(finding_embeds), 10):
        messages.append(
            {
                "embeds": finding_embeds[index : index + 10],
                "allowed_mentions": {"parse": []},
            }
        )
    return messages


class DiscordNotifier:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 3,
        sleep_fn: Callable[[float], None] = sleep,
        jitter_fn: Callable[[], float] = random,
    ) -> None:
        self._client = client or httpx.Client(timeout=20, follow_redirects=False)
        self._max_attempts = max_attempts
        self._sleep = sleep_fn
        self._jitter = jitter_fn

    def send(self, webhook_url: str, review: DiscordReview) -> list[str]:
        validate_discord_webhook_url(webhook_url)
        message_ids: list[str] = []
        for payload in format_discord_messages(review):
            response = self._post_with_retry(webhook_url, payload)
            data = response.json()
            if data.get("id") is not None:
                message_ids.append(str(data["id"]))
        return message_ids

    def _post_with_retry(self, webhook_url: str, payload: dict[str, Any]) -> httpx.Response:
        separator = "&" if "?" in webhook_url else "?"
        url = f"{webhook_url}{separator}wait=true"
        last_error = "Discord delivery failed"
        for attempt in range(self._max_attempts):
            try:
                response = self._client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError):
                response = None
                last_error = "Discord network error"
            if response is not None and response.status_code < 400:
                return response
            if response is not None and response.status_code not in {429, 500, 502, 503, 504}:
                raise PermanentDiscordError(f"Discord rejected payload ({response.status_code})")
            if attempt + 1 < self._max_attempts:
                retry_after = 2**attempt
                if response is not None and response.status_code == 429:
                    with suppress(ValueError, TypeError):
                        retry_after = float(response.json().get("retry_after", retry_after))
                self._sleep(min(retry_after + self._jitter() * 0.25, 30))
        raise DiscordDeliveryError(last_error)
