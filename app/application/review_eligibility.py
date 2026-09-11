"""Runtime eligibility policy for queued and in-flight review runs."""

from datetime import datetime


def review_ineligibility_reason(
    *,
    repository_enabled: bool,
    installation_suspended_at: datetime | None,
) -> str | None:
    if installation_suspended_at is not None:
        return "installation_suspended"
    if not repository_enabled:
        return "repository_disabled"
    return None
