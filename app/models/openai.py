"""OpenAI Responses and compatible Chat Completions adapters."""

import json
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from app.models.base import ModelOutputError, ModelProvider, ModelRequest, ModelResponse
from app.review.schemas import ModelReviewOutput

OpenAIAPIStyle = Literal["responses", "chat_completions"]
ChatResponseFormat = Literal["json_schema", "json_object", "prompt"]
ChatTokenLimitField = Literal["max_completion_tokens", "max_tokens"]


class OpenAIModelProvider:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def review(self, request: ModelRequest) -> ModelResponse:
        response = self._client.post(
            f"{self._base_url}/responses",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": request.model,
                "max_output_tokens": request.max_output_tokens,
                "instructions": request.system_prompt,
                "input": request.user_prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "pull_request_review",
                        "strict": True,
                        "schema": ModelReviewOutput.model_json_schema(),
                    }
                },
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
            output_text = _extract_output_text(payload)
            output = _parse_model_output(output_text)
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
        except (
            AttributeError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise ModelOutputError("Model output failed schema validation") from exc
        return ModelResponse(
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


class OpenAIChatCompletionsProvider:
    """OpenAI-compatible Chat Completions adapter with configurable JSON enforcement."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        response_format: ChatResponseFormat = "json_schema",
        token_limit_field: ChatTokenLimitField = "max_completion_tokens",
        timeout: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._response_format = response_format
        self._token_limit_field = token_limit_field
        self._client = client or httpx.Client(timeout=timeout)

    def review(self, request: ModelRequest) -> ModelResponse:
        schema = ModelReviewOutput.model_json_schema()
        system_prompt = request.system_prompt
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            self._token_limit_field: request.max_output_tokens,
        }
        if self._response_format == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "pull_request_review",
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            schema_instruction = (
                "\nReturn exactly one JSON object matching this schema:\n"
                f"{json.dumps(schema, separators=(',', ':'))}"
            )
            payload["messages"][0]["content"] = system_prompt + schema_instruction
            if self._response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}

        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
        )
        response.raise_for_status()
        try:
            response_payload = response.json()
            output_text = _extract_chat_output_text(response_payload)
            output = _parse_model_output(output_text)
            usage = response_payload.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise ModelOutputError("Model output failed schema validation") from exc
        return ModelResponse(
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def create_openai_provider(
    api_key: str,
    *,
    base_url: str,
    api_style: OpenAIAPIStyle,
    chat_response_format: ChatResponseFormat = "json_schema",
    chat_token_limit_field: ChatTokenLimitField = "max_completion_tokens",
) -> ModelProvider:
    if api_style == "responses":
        return OpenAIModelProvider(api_key, base_url=base_url)
    if api_style == "chat_completions":
        return OpenAIChatCompletionsProvider(
            api_key,
            base_url=base_url,
            response_format=chat_response_format,
            token_limit_field=chat_token_limit_field,
        )
    raise ValueError(f"Unsupported OpenAI API style: {api_style}")


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("OpenAI response did not contain structured output text")


def _extract_chat_output_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Chat completion did not contain choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("Chat completion choice is invalid")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("Chat completion did not contain message content")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    raise ValueError("Chat completion did not contain message content")


def _parse_model_output(output_text: str) -> ModelReviewOutput:
    stripped = output_text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 : -3].strip()
    return ModelReviewOutput.model_validate(json.loads(stripped))
