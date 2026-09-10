"""Review a local unified-diff fixture from the command line."""

import argparse
import json
import os
from pathlib import Path

from app.models.base import FakeModelProvider
from app.models.openai import OpenAIModelProvider
from app.review.diff import parse_unified_diff
from app.review.engine import ReviewEngine
from app.review.filters import prepare_diff
from app.review.schemas import ModelReviewOutput


def main() -> None:
    parser = argparse.ArgumentParser(description="Review a unified diff fixture")
    parser.add_argument("diff", type=Path)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--fake-output", type=Path)
    args = parser.parse_args()

    prepared = prepare_diff(parse_unified_diff(args.diff.read_text(encoding="utf-8")))
    if args.fake_output:
        output = ModelReviewOutput.model_validate_json(args.fake_output.read_text(encoding="utf-8"))
        provider = FakeModelProvider([output] * max(1, len(prepared.chunks)))
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error("OPENAI_API_KEY is required unless --fake-output is supplied")
        provider = OpenAIModelProvider(api_key)
    result = ReviewEngine(provider).review(prepared, model=args.model)
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
