"""Minimal GitHub Pull Request REST client."""

from collections.abc import Callable

import httpx

from app.github.schemas import GitHubFile, PullRequestData


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
