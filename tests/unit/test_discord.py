import httpx

from app.notifications.discord import DiscordNotifier, DiscordReview, format_discord_messages
from app.review.schemas import FindingSchema, ReviewResult, Severity


def make_review(count: int = 1) -> DiscordReview:
    findings = [
        FindingSchema(
            severity=Severity.HIGH,
            file=f"src/{index}.py",
            line=index + 1,
            title="@everyone unsafe input",
            explanation="Input is accepted without validation.",
            confidence=0.9,
        )
        for index in range(count)
    ]
    return DiscordReview(
        repository="octo/repo",
        pull_number=7,
        title="Validate input",
        author="octocat",
        url="https://github.com/octo/repo/pull/7",
        result=ReviewResult(summary="Review", risk=Severity.HIGH, findings=findings),
    )


def test_formatter_paginates_and_disables_mentions() -> None:
    messages = format_discord_messages(make_review(21))
    assert [len(message["embeds"]) for message in messages] == [10, 10, 2]
    assert all(message["allowed_mentions"] == {"parse": []} for message in messages)


def test_notifier_retries_rate_limit() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"retry_after": 0.01})
        return httpx.Response(200, json={"id": "message-1"})

    notifier = DiscordNotifier(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=sleeps.append,
        jitter_fn=lambda: 0,
    )
    ids = notifier.send("https://discord.com/api/webhooks/123/token", make_review())
    assert ids == ["message-1"]
    assert attempts == 2
    assert sleeps == [0.01]
