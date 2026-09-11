"""Review a local unified-diff fixture from the command line."""

import argparse
import json
import os
from pathlib import Path
from typing import cast

from app.models.base import FakeModelProvider
from app.models.openai import (
    ChatResponseFormat,
    ChatTokenLimitField,
    OpenAIAPIStyle,
    create_openai_provider,
)
from app.review.diff import parse_unified_diff
from app.review.engine import ReviewEngine
from app.review.filters import prepare_diff
from app.review.schemas import ModelReviewOutput


def main() -> None:
    parser = argparse.ArgumentParser(description="Review a unified diff fixture")
    parser.add_argument("diff", type=Path)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--fake-output", type=Path)
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument(
        "--api-style",
        choices=("responses", "chat_completions"),
        default=os.getenv("OPENAI_API_STYLE", "responses"),
    )
    parser.add_argument(
        "--chat-response-format",
        choices=("json_schema", "json_object", "prompt"),
        default=os.getenv("OPENAI_CHAT_RESPONSE_FORMAT", "json_schema"),
    )
    parser.add_argument(
        "--chat-token-limit-field",
        choices=("max_completion_tokens", "max_tokens"),
        default=os.getenv("OPENAI_CHAT_TOKEN_LIMIT_FIELD", "max_completion_tokens"),
    )
    args = parser.parse_args()

    prepared = prepare_diff(parse_unified_diff(args.diff.read_text(encoding="utf-8")))
    if args.fake_output:
        output = ModelReviewOutput.model_validate_json(args.fake_output.read_text(encoding="utf-8"))
        provider = FakeModelProvider([output] * max(1, len(prepared.chunks)))
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error("OPENAI_API_KEY is required unless --fake-output is supplied")
        provider = create_openai_provider(
            api_key,
            base_url=args.base_url,
            api_style=cast(OpenAIAPIStyle, args.api_style),
            chat_response_format=cast(ChatResponseFormat, args.chat_response_format),
            chat_token_limit_field=cast(ChatTokenLimitField, args.chat_token_limit_field),
        )
    result = ReviewEngine(provider).review(prepared, model=args.model)
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
