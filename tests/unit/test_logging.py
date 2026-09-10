"""Unit tests for app.logging (JSON format + redaction)."""

import json
import logging
import sys

from app.logging import JsonFormatter, redact


def test_redact_masks_sensitive_keys():
    out = redact({"webhook_url": "https://discord/x", "token": "t", "name": "repo"})
    assert out["webhook_url"] == "***REDACTED***"
    assert out["token"] == "***REDACTED***"
    assert out["name"] == "repo"


def test_redact_nested_and_lists():
    out = redact({"meta": {"api_token": "abc", "ok": 1}, "items": [{"secret": "s"}, "keep"]})
    assert out["meta"]["api_token"] == "***REDACTED***"
    assert out["meta"]["ok"] == 1
    assert out["items"][0]["secret"] == "***REDACTED***"
    assert out["items"][1] == "keep"


def test_json_formatter_emits_parseable_json():
    record = logging.LogRecord("app.test", logging.INFO, "p", 1, "hello", None, None)
    record.repo = "owner/name"
    record.webhook_url = "https://discord/x"
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "hello"
    assert data["level"] == "INFO"
    assert data["logger"] == "app.test"
    assert data["repo"] == "owner/name"
    assert data["webhook_url"] == "***REDACTED***"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, "p", 1, "failed", None, sys.exc_info()
        )
    data = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in data["exc_info"]
