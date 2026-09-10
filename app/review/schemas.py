"""Typed inputs and outputs for the review pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    file: str = Field(min_length=1, max_length=1000)
    line: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=4000)
    suggestion: str | None = Field(default=None, max_length=4000)
    confidence: float = Field(ge=0, le=1)


class ModelReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    risk: Severity
    findings: list[FindingSchema] = Field(default_factory=list, max_length=100)


class ReviewResult(ModelReviewOutput):
    input_tokens: int = 0
    output_tokens: int = 0
    is_partial: bool = False
    skipped_files: int = 0
    skipped_lines: int = 0


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: str
    content: str
    old_line: int | None
    new_line: int | None


@dataclass(frozen=True, slots=True)
class DiffHunk:
    header: str
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class DiffFile:
    path: str
    status: str = "modified"
    hunks: tuple[DiffHunk, ...] = ()
    is_binary: bool = False

    @property
    def reviewable_line_count(self) -> int:
        return sum(
            1
            for hunk in self.hunks
            for line in hunk.lines
            if line.new_line is not None and line.kind in {"added", "context"}
        )


@dataclass(frozen=True, slots=True)
class ReviewChunk:
    text: str
    locations: frozenset[tuple[str, int]]


@dataclass(slots=True)
class PreparedDiff:
    chunks: list[ReviewChunk] = field(default_factory=list)
    valid_locations: set[tuple[str, int]] = field(default_factory=set)
    skipped_files: int = 0
    skipped_lines: int = 0
    is_partial: bool = False
