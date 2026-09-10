import httpx

from app.authz import RepositoryRole
from app.github.oauth import GitHubOAuthClient


def oauth_client(response: httpx.Response) -> GitHubOAuthClient:
    return GitHubOAuthClient(
        "client-id",
        "client-secret",
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: response)),
    )


def test_repository_role_uses_effective_github_permissions() -> None:
    client = oauth_client(
        httpx.Response(
            200,
            json={"permissions": {"admin": False, "maintain": True, "pull": True}},
        )
    )

    assert client.repository_role("token", "octo", "repo") is RepositoryRole.MAINTAINER


def test_repository_role_denies_hidden_or_forbidden_repository() -> None:
    assert oauth_client(httpx.Response(404)).repository_role("token", "octo", "repo") is None
    assert oauth_client(httpx.Response(403)).repository_role("token", "octo", "repo") is None


def test_admin_compatibility_helper_requires_admin_role() -> None:
    admin = oauth_client(httpx.Response(200, json={"permissions": {"admin": True}}))
    viewer = oauth_client(httpx.Response(200, json={"permissions": {"pull": True}}))

    assert admin.is_repository_admin("token", "octo", "repo") is True
    assert viewer.is_repository_admin("token", "octo", "repo") is False
