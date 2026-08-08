"""Small redacting logger setup for v2 process composition."""

from __future__ import annotations

import logging
import re

_SECRET_PATTERN = re.compile(
    r"(?ix)"
    r"(?P<label>[\"']?(?:authorization|api[_-]?key|token|password|private[_-]?key|"
    r"service[_-]?role)[\"']?)\s*[:=]\s*"
    r"(?P<value>[\"']?(?:bearer\s+)?[^\s,;\]\}\"']+[\"']?)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;\]\}]+")


def _redact(value: str) -> str:
    value = _SECRET_PATTERN.sub(
        lambda match: f"{match.group('label')}=[REDACTED]", value
    )
    return _BEARER_PATTERN.sub("Bearer [REDACTED]", value)


class RedactingFilter(logging.Filter):
    """Remove obvious credentials and all exception detail from process logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        # This handler is installed at the root, so third-party records do not
        # necessarily originate from the request middleware.
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        # Formatting first means labels in a format string and their supplied
        # values are redacted together; leaving ``args`` in place would make
        # logging try to format an already-redacted string a second time.
        record.msg = _redact(message)
        record.args = ()
        # A traceback may contain unstructured provider responses, request
        # bodies, or configuration values which no regex can safely redact.
        # v2 records stable event codes and correlation IDs instead.
        record.exc_info = None
        record.exc_text = None
        return True


class RedactingFormatter(logging.Formatter):
    """Redact exception text too, because it can include provider responses."""

    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(handler)


class RequestLogAdapter(logging.LoggerAdapter):
    """Guarantee the formatter always has a correlation identifier."""

    def process(self, msg, kwargs):  # type: ignore[no-untyped-def]
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("request_id", "-")
        return msg, kwargs
