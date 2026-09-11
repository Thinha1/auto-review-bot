import json

from app.review.prompts import SYSTEM_PROMPT, build_user_prompt
from app.review.schemas import ModelReviewOutput, PreparedDiff, ReviewChunk
from app.review.usage import estimate_token_reservation


def test_estimate_reserves_prompt_and_output_ceiling_for_each_call() -> None:
    prepared = PreparedDiff(
        chunks=[
            ReviewChunk("first chunk", frozenset()),
            ReviewChunk("second chunk", frozenset()),
        ]
    )

    reservation = estimate_token_reservation(
        prepared,
        custom_instructions="Check authorization.",
        max_output_tokens_per_call=4000,
    )

    schema = json.dumps(
        ModelReviewOutput.model_json_schema(),
        separators=(",", ":"),
        sort_keys=True,
    )
    expected = sum(
        (len(SYSTEM_PROMPT + build_user_prompt(chunk.text, "Check authorization.") + schema) + 3)
        // 4
        + 4000
        for chunk in prepared.chunks
    )

    assert reservation == expected


def test_empty_review_requires_no_token_reservation() -> None:
    assert (
        estimate_token_reservation(
            PreparedDiff(),
            custom_instructions=None,
            max_output_tokens_per_call=4000,
        )
        == 0
    )
