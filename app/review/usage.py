"""Deterministic token reservation estimates for review model calls."""

import json

from app.review.prompts import SYSTEM_PROMPT, build_user_prompt
from app.review.schemas import ModelReviewOutput, PreparedDiff

_OUTPUT_SCHEMA = json.dumps(
    ModelReviewOutput.model_json_schema(),
    separators=(",", ":"),
    sort_keys=True,
)


def estimate_token_reservation(
    prepared: PreparedDiff,
    *,
    custom_instructions: str | None,
    max_output_tokens_per_call: int,
) -> int:
    """Reserve prompt/schema estimates plus each call's output ceiling."""
    total = 0
    for chunk in prepared.chunks:
        request_text = (
            SYSTEM_PROMPT + build_user_prompt(chunk.text, custom_instructions) + _OUTPUT_SCHEMA
        )
        estimated_input_tokens = (len(request_text) + 3) // 4
        total += estimated_input_tokens + max_output_tokens_per_call
    return total
