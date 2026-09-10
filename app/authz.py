"""Repository-scoped authorization policy for the dashboard."""

from collections.abc import Mapping
from enum import StrEnum


class RepositoryRole(StrEnum):
    VIEWER = "viewer"
    MAINTAINER = "maintainer"
    ADMIN = "admin"


class RepositoryAction(StrEnum):
    VIEW = "view"
    OPERATE = "operate"
    CONFIGURE = "configure"


ROLE_GRANTS: dict[RepositoryRole, frozenset[RepositoryAction]] = {
    RepositoryRole.VIEWER: frozenset({RepositoryAction.VIEW}),
    RepositoryRole.MAINTAINER: frozenset({RepositoryAction.VIEW, RepositoryAction.OPERATE}),
    RepositoryRole.ADMIN: frozenset(RepositoryAction),
}


def role_from_github_permissions(
    permissions: Mapping[str, object],
) -> RepositoryRole | None:
    """Map GitHub's effective repository permissions to application roles."""
    if permissions.get("admin") is True:
        return RepositoryRole.ADMIN
    if permissions.get("maintain") is True or permissions.get("push") is True:
        return RepositoryRole.MAINTAINER
    if permissions.get("triage") is True or permissions.get("pull") is True:
        return RepositoryRole.VIEWER
    return None


def role_allows(role: RepositoryRole, action: RepositoryAction) -> bool:
    return action in ROLE_GRANTS[role]
