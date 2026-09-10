"""JSON logging with redaction of sensitive fields.

Secrets (webhook URLs, tokens, keys, ...) must never reach the log output.
Structured fields passed via ``logger.info("msg", extra={...})`` are serialized
as JSON; any field whose name looks sensitive is replaced with a redaction marker.
"""

import json
import logging
from typing import Any

_REDACTED = "***REDACTED***"

_SENSITIVE_MARKERS = (
    "secret",
    "token",
    "password",
    "passwd",
    "webhook",
    "authorization",
    "credential",
    "api_key",
)

# LogRecord attributes that are not user-provided fields.
_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive dict keys masked."""
    if isinstance(value, dict):
        return {k: (_REDACTED if is_sensitive_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Emit each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install a JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
