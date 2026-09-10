"""GitHub App JWT creation and installation-token exchange."""

from datetime import UTC, datetime, timedelta

import httpx
import jwt


class GitHubAppAuth:
    def __init__(
        self,
        app_id: str,
        private_key: str,
        *,
        api_url: str = "https://api.github.com",
        client: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id
        self.private_key = private_key.replace("\\n", "\n")
        self.api_url = api_url.rstrip("/")
        self.client = client or httpx.Client(timeout=20)

    def app_jwt(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        payload = {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def installation_token(self, installation_id: int) -> str:
        response = self.client.post(
            f"{self.api_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not isinstance(token, str):
            raise ValueError("GitHub installation token response is invalid")
        return token
