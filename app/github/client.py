"""GitHub REST client for pull request data and review checks."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.github.schemas import GitHubFile, PullRequestData
from app.review.schemas import FindingSchema, ReviewResult, Severity

CHECK_NAME = "AI PR Review"
MAX_ANNOTATIONS_PER_REQUEST = 50


@dataclass(frozen=True, slots=True)
class PublishedCheck:
    id: int
    url: str
    conclusion: str


def _annotation_level(severity: Severity) -> str:
    if severity in {Severity.CRITICAL, Severity.HIGH}:
        return "failure"
    if severity is Severity.MEDIUM:
        return "warning"
    return "notice"


def _check_conclusion(result: ReviewResult) -> str:
    if any(finding.severity in {Severity.CRITICAL, Severity.HIGH} for finding in result.findings):
        return "action_required"
    if result.findings:
        return "neutral"
    return "success"


def _annotation(finding: FindingSchema) -> dict[str, Any]:
    message = finding.explanation
    if finding.suggestion:
        message += f"\n\nSuggestion: {finding.suggestion}"
    return {
        "path": finding.file,
        "start_line": finding.line,
        "end_line": finding.line,
        "annotation_level": _annotation_level(finding.severity),
        "title": finding.title[:255],
        "message": message[:65_535],
    }


def _check_summary(result: ReviewResult) -> str:
    partial = " Partial review." if result.is_partial else ""
    counts = f"{len(result.findings)} finding(s); overall risk: {result.risk.value}."
    skipped = ""
    if result.is_partial:
        skipped = (
            f" Skipped {result.skipped_files} file(s) and {result.skipped_lines} diff line(s)."
        )
    return f"{result.summary}\n\n{counts}{partial}{skipped}"[:65_535]


class GitHubClient:
    def __init__(
        self,
        token: str | Callable[[], str],
        *,
        api_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)

    def _headers(self) -> dict[str, str]:
        token = self._token() if callable(self._token) else self._token
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_pull(self, owner: str, name: str, pull_number: int) -> PullRequestData:
        base = f"{self._api_url}/repos/{owner}/{name}/pulls/{pull_number}"
        response = self._client.get(base, headers=self._headers())
        response.raise_for_status()
        pull = response.json()
        files, truncated = self._list_files(base)
        return PullRequestData(
            number=int(pull["number"]),
            title=str(pull["title"]),
            author=str(pull["user"]["login"]),
            url=str(pull["html_url"]),
            head_sha=str(pull["head"]["sha"]),
            base_sha=str(pull["base"]["sha"]),
            files=tuple(files),
            files_truncated=truncated,
        )

    def get_head_sha(self, owner: str, name: str, pull_number: int) -> str:
        response = self._client.get(
            f"{self._api_url}/repos/{owner}/{name}/pulls/{pull_number}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return str(response.json()["head"]["sha"])

    def publish_review_check(
        self,
        owner: str,
        name: str,
        head_sha: str,
        details_url: str,
        result: ReviewResult,
    ) -> PublishedCheck:
        """Publish one completed check run, appending annotations in API-sized batches."""
        endpoint = f"{self._api_url}/repos/{owner}/{name}/check-runs"
        response = self._client.post(
            endpoint,
            headers=self._headers(),
            json={
                "name": CHECK_NAME,
                "head_sha": head_sha,
                "status": "in_progress",
                "details_url": details_url,
            },
        )
        response.raise_for_status()
        created = response.json()
        check_run_id = int(created["id"])
        check_url = str(created.get("html_url") or details_url)
        conclusion = _check_conclusion(result)
        annotations = [_annotation(finding) for finding in result.findings]
        batches = [
            annotations[index : index + MAX_ANNOTATIONS_PER_REQUEST]
            for index in range(0, len(annotations), MAX_ANNOTATIONS_PER_REQUEST)
        ] or [[]]
        update_endpoint = f"{endpoint}/{check_run_id}"
        for index, batch in enumerate(batches):
            is_last = index == len(batches) - 1
            payload: dict[str, Any] = {
                "status": "completed" if is_last else "in_progress",
                "output": {
                    "title": f"AI review: {result.risk.value} risk",
                    "summary": _check_summary(result),
                    "annotations": batch,
                },
            }
            if is_last:
                payload["conclusion"] = conclusion
                payload["completed_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            update = self._client.patch(
                update_endpoint,
                headers=self._headers(),
                json=payload,
            )
            update.raise_for_status()
        return PublishedCheck(id=check_run_id, url=check_url, conclusion=conclusion)

    def _list_files(self, pull_url: str) -> tuple[list[GitHubFile], bool]:
        files: list[GitHubFile] = []
        for page in range(1, 31):
            response = self._client.get(
                f"{pull_url}/files",
                headers=self._headers(),
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            items = response.json()
            files.extend(
                GitHubFile(
                    path=str(item["filename"]),
                    status=str(item["status"]),
                    patch=item.get("patch"),
                    changes=int(item.get("changes", 0)),
                )
                for item in items
            )
            if len(items) < 100:
                return files, False
        return files, True
