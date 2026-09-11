"""Model provider port and deterministic fake."""

from dataclasses import dataclass
from typing import Protocol

from app.review.schemas import ModelReviewOutput


class ModelOutputError(RuntimeError):
    """The provider returned data that failed the configured output contract."""


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system_prompt: str
    user_prompt: str
    model: str
    max_output_tokens: int = 4000


@dataclass(frozen=True, slots=True)
class ModelResponse:
    output: ModelReviewOutput
    input_tokens: int = 0
    output_tokens: int = 0


class ModelProvider(Protocol):
    def review(self, request: ModelRequest) -> ModelResponse: ...


class FakeModelProvider:
    """A predictable provider for tests and local fixture runs."""

    def __init__(self, responses: list[ModelReviewOutput]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def review(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("Fake model has no response configured")
        output = self.responses.pop(0)
        return ModelResponse(output=output, input_tokens=100, output_tokens=50)
