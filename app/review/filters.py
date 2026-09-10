"""Deterministic path filtering, limiting, and diff chunking."""

from fnmatch import fnmatch
from pathlib import PurePosixPath

from app.review.schemas import DiffFile, PreparedDiff, ReviewChunk

DEFAULT_IGNORED_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.crt",
    "vendor/*",
    "*/vendor/*",
    "node_modules/*",
    "*/node_modules/*",
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "*.min.js",
    "*.min.css",
    "*.map",
)
LOCK_FILE_PATTERNS = ("*lock.json", "*.lock", "pnpm-lock.yaml", "yarn.lock")


def is_ignored_path(
    path: str, patterns: list[str] | tuple[str, ...] = (), *, include_lock_files: bool = False
) -> bool:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    candidates = (*DEFAULT_IGNORED_PATTERNS, *patterns)
    if not include_lock_files:
        candidates = (*candidates, *LOCK_FILE_PATTERNS)
    return any(fnmatch(normalized, pattern) for pattern in candidates)


def _render_file(
    file: DiffFile, remaining_lines: int
) -> tuple[list[tuple[str, tuple[str, int] | None]], int]:
    rendered: list[tuple[str, tuple[str, int] | None]] = [
        (f"FILE: {file.path} ({file.status})", None)
    ]
    used = 0
    for hunk in file.hunks:
        if used >= remaining_lines:
            break
        rendered.append((hunk.header, None))
        for line in hunk.lines:
            if used >= remaining_lines:
                break
            marker = {"added": "+", "removed": "-", "context": " "}[line.kind]
            old = "" if line.old_line is None else str(line.old_line)
            new = "" if line.new_line is None else str(line.new_line)
            location = (
                (file.path, line.new_line)
                if line.new_line is not None and line.kind in {"added", "context"}
                else None
            )
            rendered.append((f"{old:>6} {new:>6} {marker}{line.content}", location))
            used += 1
    return rendered, used


def prepare_diff(
    files: list[DiffFile],
    *,
    ignored_paths: list[str] | None = None,
    max_files: int = 100,
    max_diff_lines: int = 5000,
    max_input_tokens: int = 50_000,
    max_model_calls: int = 20,
    include_lock_files: bool = False,
) -> PreparedDiff:
    """Filter and chunk files while preserving valid HEAD line locations."""
    result = PreparedDiff()
    patterns = ignored_paths or []
    candidates: list[DiffFile] = []
    for file in files:
        if file.is_binary or is_ignored_path(
            file.path, patterns, include_lock_files=include_lock_files
        ):
            result.skipped_files += 1
            result.skipped_lines += file.reviewable_line_count
        else:
            candidates.append(file)

    if len(candidates) > max_files:
        omitted = candidates[max_files:]
        result.skipped_files += len(omitted)
        result.skipped_lines += sum(file.reviewable_line_count for file in omitted)
        candidates = candidates[:max_files]

    line_budget = max_diff_lines
    char_budget = max(1000, max_input_tokens * 4)
    for index, file in enumerate(candidates):
        if line_budget <= 0:
            result.skipped_files += len(candidates) - index
            result.skipped_lines += sum(
                candidate.reviewable_line_count for candidate in candidates[index:]
            )
            break
        rendered_lines, used = _render_file(file, line_budget)
        if used == 0:
            continue
        if used < sum(len(hunk.lines) for hunk in file.hunks):
            result.skipped_lines += sum(len(hunk.lines) for hunk in file.hunks) - used
        line_budget -= used

        # Very large files are split by rendered lines. Each chunk remains independently
        # attributable because every line includes its old/new line number.
        current: list[tuple[str, tuple[str, int] | None]] = []
        current_size = 0
        file_header = rendered_lines[0]
        for rendered_line in rendered_lines:
            size = len(rendered_line[0]) + 1
            if current and current_size + size > char_budget:
                chunk_text = "\n".join(item[0] for item in current)
                chunk_locations = frozenset(item[1] for item in current if item[1] is not None)
                result.chunks.append(ReviewChunk(chunk_text, chunk_locations))
                current = [file_header, rendered_line]
                current_size = len(file_header[0]) + size + 1
            else:
                current.append(rendered_line)
                current_size += size
        if current:
            chunk_text = "\n".join(item[0] for item in current)
            chunk_locations = frozenset(item[1] for item in current if item[1] is not None)
            result.chunks.append(ReviewChunk(chunk_text, chunk_locations))

    if len(result.chunks) > max_model_calls:
        omitted_chunks = result.chunks[max_model_calls:]
        result.skipped_lines += len(set().union(*(chunk.locations for chunk in omitted_chunks)))
        result.chunks = result.chunks[:max_model_calls]

    result.valid_locations = {location for chunk in result.chunks for location in chunk.locations}

    result.is_partial = result.skipped_files > 0 or result.skipped_lines > 0
    return result
