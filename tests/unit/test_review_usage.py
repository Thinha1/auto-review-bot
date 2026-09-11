from app.review.schemas import PreparedDiff, ReviewChunk
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

    assert reservation > 8000


def test_empty_review_requires_no_token_reservation() -> None:
    assert (
        estimate_token_reservation(
            PreparedDiff(),
            custom_instructions=None,
            max_output_tokens_per_call=4000,
        )
        == 0
    )
