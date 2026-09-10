"""Unified-diff parsing independent from GitHub and the model provider."""

import re

from app.review.schemas import DiffFile, DiffHunk, DiffLine

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@"
)


def parse_file_patch(path: str, patch: str | None, status: str = "modified") -> DiffFile:
    if not patch or patch.startswith("Binary files "):
        return DiffFile(path=path, status=status, is_binary=True)

    hunks: list[DiffHunk] = []
    current_header: str | None = None
    current_lines: list[DiffLine] = []
    old_line = 0
    new_line = 0

    def finish_hunk() -> None:
        nonlocal current_header, current_lines
        if current_header is not None:
            hunks.append(DiffHunk(current_header, tuple(current_lines)))
        current_header = None
        current_lines = []

    for raw_line in patch.splitlines():
        match = _HUNK_HEADER.match(raw_line)
        if match:
            finish_hunk()
            current_header = raw_line
            old_line = int(match.group("old"))
            new_line = int(match.group("new"))
            continue
        if current_header is None or raw_line.startswith("\\ No newline"):
            continue
        prefix = raw_line[:1]
        content = raw_line[1:]
        if prefix == "+":
            current_lines.append(DiffLine("added", content, None, new_line))
            new_line += 1
        elif prefix == "-":
            current_lines.append(DiffLine("removed", content, old_line, None))
            old_line += 1
        else:
            current_lines.append(
                DiffLine("context", raw_line[1:] if prefix == " " else raw_line, old_line, new_line)
            )
            old_line += 1
            new_line += 1
    finish_hunk()
    return DiffFile(path=path, status=status, hunks=tuple(hunks))


def parse_unified_diff(diff: str) -> list[DiffFile]:
    """Parse a multi-file Git diff into reviewable files."""
    files: list[DiffFile] = []
    path: str | None = None
    status = "modified"
    patch_lines: list[str] = []

    def finish_file() -> None:
        nonlocal path, status, patch_lines
        if path is not None:
            files.append(parse_file_patch(path, "\n".join(patch_lines), status))
        path = None
        status = "modified"
        patch_lines = []

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish_file()
            parts = line.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else line.rsplit(" ", 1)[-1].removeprefix("b/")
        elif line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "removed"
        elif line.startswith("rename to "):
            path = line.removeprefix("rename to ")
            status = "renamed"
        elif path is not None:
            patch_lines.append(line)
    finish_file()
    return files
