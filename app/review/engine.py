"""Review orchestration and defense-in-depth output validation."""

import hashlib
import re

from app.models.base import ModelProvider, ModelRequest
from app.review.prompts import SYSTEM_PROMPT, build_user_prompt
from app.review.schemas import FindingSchema, PreparedDiff, ReviewResult, Severity

_SEVERITY_ORDER = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


def finding_fingerprint(finding: FindingSchema) -> str:
    normalized_title = re.sub(r"\W+", " ", finding.title.lower()).strip()
    raw = f"{finding.file}:{finding.line}:{normalized_title}"
    return hashlib.sha256(raw.encode()).hexdigest()


class ReviewEngine:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def review(
        self,
        prepared: PreparedDiff,
        *,
        model: str,
        max_findings: int = 20,
        minimum_severity: Severity = Severity.LOW,
        custom_instructions: str | None = None,
    ) -> ReviewResult:
        findings_by_fingerprint: dict[str, FindingSchema] = {}
        summaries: list[str] = []
        input_tokens = 0
        output_tokens = 0

        for chunk in prepared.chunks:
            response = self.provider.review(
                ModelRequest(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=build_user_prompt(chunk.text, custom_instructions),
                    model=model,
                )
            )
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            summaries.append(response.output.summary)
            for finding in response.output.findings:
                if (finding.file, finding.line) not in chunk.locations:
                    continue
                if _SEVERITY_ORDER[finding.severity] < _SEVERITY_ORDER[minimum_severity]:
                    continue
                fingerprint = finding_fingerprint(finding)
                previous = findings_by_fingerprint.get(fingerprint)
                if previous is None or finding.confidence > previous.confidence:
                    findings_by_fingerprint[fingerprint] = finding

        findings = sorted(
            findings_by_fingerprint.values(),
            key=lambda finding: (_SEVERITY_ORDER[finding.severity], finding.confidence),
            reverse=True,
        )[:max_findings]
        risk = max(
            (finding.severity for finding in findings),
            key=lambda severity: _SEVERITY_ORDER[severity],
            default=Severity.LOW,
        )
        summary = " ".join(dict.fromkeys(summaries)) or "No reviewable changes."
        return ReviewResult(
            summary=summary[:2000],
            risk=risk,
            findings=findings,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_partial=prepared.is_partial,
            skipped_files=prepared.skipped_files,
            skipped_lines=prepared.skipped_lines,
        )
