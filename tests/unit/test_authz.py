import pytest

from app.authz import (
    RepositoryAction,
    RepositoryRole,
    role_allows,
    role_from_github_permissions,
)


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        ({"admin": True, "push": True, "pull": True}, RepositoryRole.ADMIN),
        ({"maintain": True, "pull": True}, RepositoryRole.MAINTAINER),
        ({"push": True, "pull": True}, RepositoryRole.MAINTAINER),
        ({"triage": True, "pull": True}, RepositoryRole.VIEWER),
        ({"pull": True}, RepositoryRole.VIEWER),
        ({}, None),
    ],
)
def test_maps_effective_github_permissions_to_role(
    permissions: dict[str, object], expected: RepositoryRole | None
) -> None:
    assert role_from_github_permissions(permissions) is expected


@pytest.mark.parametrize(
    ("role", "action", "expected"),
    [
        (RepositoryRole.VIEWER, RepositoryAction.VIEW, True),
        (RepositoryRole.VIEWER, RepositoryAction.OPERATE, False),
        (RepositoryRole.VIEWER, RepositoryAction.CONFIGURE, False),
        (RepositoryRole.MAINTAINER, RepositoryAction.VIEW, True),
        (RepositoryRole.MAINTAINER, RepositoryAction.OPERATE, True),
        (RepositoryRole.MAINTAINER, RepositoryAction.CONFIGURE, False),
        (RepositoryRole.ADMIN, RepositoryAction.VIEW, True),
        (RepositoryRole.ADMIN, RepositoryAction.OPERATE, True),
        (RepositoryRole.ADMIN, RepositoryAction.CONFIGURE, True),
    ],
)
def test_repository_role_action_matrix(
    role: RepositoryRole, action: RepositoryAction, expected: bool
) -> None:
    assert role_allows(role, action) is expected
