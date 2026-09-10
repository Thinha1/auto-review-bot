import json
from typing import Any

import httpx

from app.github.client import GitHubClient
from app.review.schemas import FindingSchema, ReviewResult, Severity


def test_github_client_fetches_pull_and_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files"):
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "src/a.py",
                        "status": "modified",
                        "patch": "@@ -1 +1 @@\n-old\n+new",
                        "changes": 2,
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "number": 7,
                "title": "Change",
                "user": {"login": "octo"},
                "html_url": "https://github.com/octo/repo/pull/7",
                "head": {"sha": "head"},
                "base": {"sha": "base"},
            },
        )

    client = GitHubClient("token", client=httpx.Client(transport=httpx.MockTransport(handler)))
    pull = client.get_pull("octo", "repo", 7)
    assert pull.head_sha == "head"
    assert pull.files[0].path == "src/a.py"
    assert pull.files_truncated is False


def finding(index: int, severity: Severity = Severity.MEDIUM) -> FindingSchema:
    return FindingSchema(
        severity=severity,
        file="src/a.py",
        line=index + 1,
        title=f"Finding {index}",
        explanation="Explain the problem.",
        suggestion="Fix the problem.",
        confidence=0.9,
    )


def test_github_client_publishes_check_with_annotations() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"id": 42, "html_url": "https://github.com/octo/repo/runs/42"},
            )
        return httpx.Response(200, json={"id": 42})

    client = GitHubClient("token", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = ReviewResult(
        summary="Authentication can be bypassed.",
        risk=Severity.HIGH,
        findings=[finding(0, Severity.HIGH)],
        is_partial=True,
        skipped_files=1,
        skipped_lines=20,
    )

    published = client.publish_review_check(
        "octo", "repo", "head", "https://github.com/octo/repo/pull/7", result
    )

    assert published.id == 42
    assert published.conclusion == "action_required"
    assert [request.method for request in requests] == ["POST", "PATCH"]
    created = json.loads(requests[0].content)
    completed = json.loads(requests[1].content)
    assert created == {
        "name": "AI PR Review",
        "head_sha": "head",
        "status": "in_progress",
        "details_url": "https://github.com/octo/repo/pull/7",
    }
    assert completed["status"] == "completed"
    assert completed["conclusion"] == "action_required"
    assert completed["output"]["annotations"] == [
        {
            "path": "src/a.py",
            "start_line": 1,
            "end_line": 1,
            "annotation_level": "failure",
            "title": "Finding 0",
            "message": "Explain the problem.\n\nSuggestion: Fix the problem.",
        }
    ]
    assert "Partial review" in completed["output"]["summary"]


def test_github_client_appends_annotations_in_batches_of_fifty() -> None:
    updates: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": 42})
        updates.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 42})

    client = GitHubClient("token", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = ReviewResult(
        summary="Many findings.",
        risk=Severity.MEDIUM,
        findings=[finding(index) for index in range(51)],
    )

    published = client.publish_review_check(
        "octo", "repo", "head", "https://github.com/octo/repo/pull/7", result
    )

    assert published.conclusion == "neutral"
    assert [len(update["output"]["annotations"]) for update in updates] == [50, 1]
    assert updates[0]["status"] == "in_progress"
    assert updates[1]["status"] == "completed"
