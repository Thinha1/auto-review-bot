"""Data returned by the GitHub adapter."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GitHubFile:
    path: str
    status: str
    patch: str | None
    changes: int = 0


@dataclass(frozen=True, slots=True)
class PullRequestData:
    number: int
    title: str
    author: str
    url: str
    head_sha: str
    base_sha: str
    files: tuple[GitHubFile, ...]
    files_truncated: bool = False
