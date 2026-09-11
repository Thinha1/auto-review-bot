import json

import httpx
import pytest

from app.models.base import ModelOutputError, ModelRequest
from app.models.openai import (
    OpenAIChatCompletionsProvider,
    OpenAIModelProvider,
    create_openai_provider,
)


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
    assert captured["max_output_tokens"] == 4000
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


def test_chat_completions_provider_uses_strict_json_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"summary":"OK","risk":"low","findings":[]}',
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            },
        )

    provider = OpenAIChatCompletionsProvider(
        "test-key",
        base_url="https://compatible.example/v1/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.review(ModelRequest("system", "user", "compatible-model", 2048))

    assert result.output.summary == "OK"
    assert result.input_tokens == 12
    assert result.output_tokens == 6
    assert captured["max_completion_tokens"] == 2048
    assert captured["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert captured["response_format"]["json_schema"]["strict"] is True  # type: ignore[index]


@pytest.mark.parametrize("response_format", ["json_object", "prompt"])
def test_chat_completions_provider_supports_legacy_json_modes(
    response_format: str,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"summary":"OK","risk":"low","findings":[]}'}}]
            },
        )

    provider = OpenAIChatCompletionsProvider(
        "test-key",
        response_format=response_format,  # type: ignore[arg-type]
        token_limit_field="max_tokens",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider.review(ModelRequest("system", "user", "compatible-model", 1024))

    assert captured["max_tokens"] == 1024
    assert "matching this schema" in captured["messages"][0]["content"]  # type: ignore[index]
    if response_format == "json_object":
        assert captured["response_format"] == {"type": "json_object"}
    else:
        assert "response_format" not in captured


def test_chat_completions_provider_classifies_missing_content() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"choices": [{"message": {}}]})
        )
    )
    provider = OpenAIChatCompletionsProvider("test-key", client=client)

    with pytest.raises(ModelOutputError):
        provider.review(ModelRequest("system", "user", "compatible-model"))


def test_chat_completions_provider_accepts_fenced_content_parts_then_validates() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "```json\n"},
                                    {
                                        "type": "text",
                                        "text": '{"summary":"OK","risk":"low","findings":[]}\n```',
                                    },
                                ]
                            }
                        }
                    ]
                },
            )
        )
    )
    provider = OpenAIChatCompletionsProvider("test-key", response_format="prompt", client=client)

    result = provider.review(ModelRequest("system", "user", "compatible-model"))

    assert result.output.summary == "OK"


def test_provider_factory_preserves_responses_default_and_selects_chat() -> None:
    assert isinstance(
        create_openai_provider(
            "test-key", base_url="https://api.openai.com/v1", api_style="responses"
        ),
        OpenAIModelProvider,
    )
    assert isinstance(
        create_openai_provider(
            "test-key",
            base_url="https://compatible.example/v1",
            api_style="chat_completions",
        ),
        OpenAIChatCompletionsProvider,
    )
    with pytest.raises(ValueError, match="Unsupported"):
        create_openai_provider(
            "test-key",
            base_url="https://compatible.example/v1",
            api_style="invalid",  # type: ignore[arg-type]
        )
