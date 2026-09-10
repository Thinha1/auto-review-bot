import json

import httpx
import pytest

from app.models.base import ModelOutputError, ModelRequest
from app.models.openai import OpenAIModelProvider


def test_openai_provider_uses_strict_json_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        parsed: dict[str, object] = json.loads(request.content)
        captured.update(parsed)
        return httpx.Response(
            200,
            json={
                "output_text": '{"summary":"OK","risk":"low","findings":[]}',
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIModelProvider("test-key", client=client)
    result = provider.review(ModelRequest("system", "user", "gpt-test"))
    assert result.output.summary == "OK"
    assert result.input_tokens == 10
    assert captured["text"]["format"]["strict"] is True  # type: ignore[index]


def test_openai_provider_classifies_invalid_schema() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"output_text": "not-json"})
        )
    )
    provider = OpenAIModelProvider("test-key", client=client)
    with pytest.raises(ModelOutputError):
        provider.review(ModelRequest("system", "user", "gpt-test"))
