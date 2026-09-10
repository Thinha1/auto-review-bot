"""OpenAI Responses API adapter using strict Structured Outputs."""

import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.models.base import ModelOutputError, ModelRequest, ModelResponse
from app.review.schemas import ModelReviewOutput


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
            output = ModelReviewOutput.model_validate(json.loads(output_text))
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ModelOutputError("Model output failed schema validation") from exc
        return ModelResponse(
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("OpenAI response did not contain structured output text")
