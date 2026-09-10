import httpx

from app.github.client import GitHubClient


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
