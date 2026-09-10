import httpx

from app.models.base import ModelOutputError
from app.worker import is_transient_failure


def status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


def test_transient_failure_classification() -> None:
    assert is_transient_failure(httpx.ConnectTimeout("timeout"))
    assert is_transient_failure(ModelOutputError("invalid structured output"))
    assert is_transient_failure(status_error(429))
    assert is_transient_failure(status_error(503))


def test_permanent_failure_classification() -> None:
    assert not is_transient_failure(status_error(404))
    assert not is_transient_failure(ValueError("invalid configuration"))
