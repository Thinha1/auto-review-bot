from app.review.diff import parse_file_patch, parse_unified_diff
from app.review.filters import is_ignored_path, prepare_diff


def test_parse_patch_tracks_head_lines() -> None:
    file = parse_file_patch(
        "src/example.py",
        "@@ -2,2 +2,3 @@\n context\n+added\n-old\n tail",
    )
    lines = file.hunks[0].lines
    assert [(line.kind, line.old_line, line.new_line) for line in lines] == [
        ("context", 2, 2),
        ("added", None, 3),
        ("removed", 3, None),
        ("context", 4, 4),
    ]


def test_parse_multifile_diff_and_filter_secret() -> None:
    diff = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-bad
+good
diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -0,0 +1 @@
+TOKEN=secret
"""
    prepared = prepare_diff(parse_unified_diff(diff))
    assert len(prepared.chunks) == 1
    assert ("src/a.py", 1) in prepared.valid_locations
    assert prepared.skipped_files == 1
    assert prepared.is_partial is True


def test_path_filter_defaults_and_custom_patterns() -> None:
    assert is_ignored_path("node_modules/a.js")
    assert is_ignored_path("secrets/server.pem")
    assert is_ignored_path("docs/generated.md", ["docs/*"])
    assert is_ignored_path("uv.lock")
    assert not is_ignored_path("uv.lock", include_lock_files=True)


def test_prepare_diff_reports_truncation() -> None:
    files = [parse_file_patch(f"src/{index}.py", "@@ -0,0 +1 @@\n+line") for index in range(3)]
    prepared = prepare_diff(files, max_files=1)
    assert len(prepared.chunks) == 1
    assert prepared.skipped_files == 2
    assert prepared.skipped_lines == 2
    assert prepared.is_partial


def test_prepare_diff_caps_model_calls_and_locations_per_chunk() -> None:
    patch = "@@ -0,0 +1,40 @@\n" + "\n".join(f"+line {index} {'x' * 80}" for index in range(1, 41))
    prepared = prepare_diff(
        [parse_file_patch("src/large.py", patch)],
        max_input_tokens=250,
        max_model_calls=1,
    )
    assert len(prepared.chunks) == 1
    assert prepared.valid_locations == set(prepared.chunks[0].locations)
    assert prepared.skipped_lines > 0
    assert prepared.is_partial
