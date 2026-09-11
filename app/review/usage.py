"""Deterministic token reservation estimates for review model calls."""

from app.review.prompts import SYSTEM_PROMPT, build_user_prompt
from app.review.schemas import PreparedDiff


def estimate_token_reservation(
    prepared: PreparedDiff,
    *,
    custom_instructions: str | None,
    max_output_tokens_per_call: int,
) -> int:
    """Reserve conservative prompt estimates plus each call's output ceiling."""
    total = 0
    for chunk in prepared.chunks:
        prompt = SYSTEM_PROMPT + build_user_prompt(chunk.text, custom_instructions)
        estimated_input_tokens = (len(prompt) + 3) // 4
        total += estimated_input_tokens + max_output_tokens_per_call
    return total
