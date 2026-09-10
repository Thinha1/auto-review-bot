"""GitHub OAuth client used by the dashboard authorization boundary."""

from urllib.parse import urlencode

import httpx


class GitHubOAuthClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        api_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_url = api_url.rstrip("/")
        self.client = client or httpx.Client(timeout=20)

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": "read:user repo",
                "state": state,
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}"

    def exchange_code(self, code: str, redirect_uri: str) -> str:
        response = self.client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str):
            raise ValueError("GitHub OAuth response did not include an access token")
        return token

    def get_login(self, access_token: str) -> str:
        response = self.client.get(
            f"{self.api_url}/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return str(response.json()["login"])

    def is_repository_admin(self, access_token: str, owner: str, name: str) -> bool:
        response = self.client.get(
            f"{self.api_url}/repos/{owner}/{name}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code in {403, 404}:
            return False
        response.raise_for_status()
        permissions = response.json().get("permissions") or {}
        return bool(permissions.get("admin"))
