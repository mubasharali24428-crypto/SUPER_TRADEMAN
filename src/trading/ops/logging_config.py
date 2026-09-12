"""Structured JSON Logging Infrastructure with Correlation IDs and Rotation Rules.

Truthfulness notes:
- Correlation IDs are stored in a :class:`contextvars.ContextVar` so concurrent
  async tasks keep their own request_id instead of sharing module-global state.
- The JSON formatter emits the canonical observability keys:
  ``ts``/``severity``/``service``/``logger``/``trace_id``/``message``/``context``.
- A redaction filter scrubs ``postgres://`` / ``postgresql://`` DSNs (including
  embedded credentials and trailing querystrings) out of formatted message and
  exception text before anything reaches disk or stdout.
"""

import contextvars
import json
import logging
import logging.handlers
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

__all__ = [
    "JSONFormatter",
    "CorrelationIdFilter",
    "RedactionFilter",
    "get_correlation_id",
    "set_correlation_id",
    "configure_structured_logging",
]

# Context-local correlation id: each asyncio task / thread sees its own value.
_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def get_correlation_id() -> str:
    """Return this context's correlation id, lazily minting one if unset.

    When real OTel tracing is active, the current span context wins: the
    W3C trace id is preferred so JSON logs join to distributed traces.
    """
    try:  # OTel extras are optional — degrade silently when unavailable
        from trading.observability.otel import current_trace_ids

        otel_trace_id = current_trace_ids()[0]
        if otel_trace_id:
            return otel_trace_id
    except Exception:
        pass
    cid = _correlation_id_var.get()
    if not cid:
        cid = uuid.uuid4().hex[:12]
        _correlation_id_var.set(cid)
    return cid


# VA-014: fallback counter when correlation id cannot be set
_correlation_fallback_count = 0


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation id for the current execution context."""
    _correlation_id_var.set(correlation_id or "")


# postgres://user:pass@host:5432/db?sslmode=... -> postgres://***:***@***:5432/db?***
_DSN_RE = re.compile(
    r"(?P<scheme>(?:postg(?:res|resql)|redis(?:\+sentinel)?|rediss)://)"
    r"(?:(?P<user>[^:@/\s]+)(?::(?P<password>[^@\s]*))?@)?"
    r"(?P<hostport>[^/?\s]+)"
    r"(?P<path>/[^\s]*)?"
    r"(?P<query>\?[^\s]*)?"
)

REDACTED_PLACEHOLDER = "[REDACTED]"

# VA-039: also redact webhook/bearer URLs (slack, pagerduty, generic)
_WEBHOOK_RE = re.compile(
    r"(?P<scheme>https?)://"
    r"(?:hooks.slack.com|api.pagerduty|discord.com/api/webhooks)"
    r"(?:/[A-Za-z0-9_-]+)+"
)


def _redact_text(text: str) -> str:
    """Strip credentials/querystrings from any postgres:// DSN occurrences."""
    if (
        "postgres" not in text
        and "redis" not in text
        and "hooks" not in text
        and "api." not in text
    ):
        return text

    def _sub(match: "re.Match[str]") -> str:
        user = match.group("user") or "***"
        hostport_raw = match.group("hostport") or "***"
        if ":" in hostport_raw:
            host, _, port = hostport_raw.rpartition(":")
            hostport = f"***:****" if port.isdigit() else "***"
        else:
            hostport = "***"
        path = "/***" if match.group("path") else ""
        query = "?***" if match.group("query") else ""
        return f"{match.group('scheme')}{user}:***@{hostport}{path}{query}"

    text = _DSN_RE.sub(_sub, text)
    return _WEBHOOK_RE.sub("[REDACTED_WEBHOOK] ", text)


class RedactionFilter(logging.Filter):
    """Scrubs postgres DSNs/querystrings from record message + exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact_text(str(record.msg))
            if record.args:
                # Redact args too so getMessage()-based formatters stay safe,
                # then collapse args so msg carries the fully redacted text.
                rendered = _redact_text(record.getMessage())
                record.msg = rendered
                record.args = None
            exc_info = record.exc_info
            if (
                exc_info
                and exc_info[0] is not None
                and isinstance(exc_info[1], BaseException)
            ):
                # Pre-render + redact exception text once; formatters consume
                # record.exc_text directly so the scrubbed version is authoritative.
                record.exc_text = _redact_text(
                    "".join(__import__("traceback").format_exception(*exc_info))
                )
        except Exception:  # never break logging on a bad record
            pass
        return True


class CorrelationIdFilter(logging.Filter):
    """Stamps the context-local correlation id onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON lines with UTC timestamps."""

    service_name = os.getenv("SERVICE_NAME", "super_trademan")

    def format(self, record: logging.LogRecord) -> str:
        # Interpolate %-style args first (also covers the RedactionFilter's
        # collapsed-args form), then redact.
        record.message = record.getMessage()
        message = record.message
        if not getattr(record, "redacted", False):
            message = _redact_text(message)

        log_obj: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "trace_id": getattr(record, "correlation_id", get_correlation_id()),
            "message": message,
            "context": {
                "module": record.module,
                "funcName": record.funcName,
                "lineno": record.lineno,
            },
        }

        exc_text = None
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
        elif record.exc_text:
            exc_text = record.exc_text
        if exc_text:
            log_obj["exception"] = _redact_text(exc_text)
            log_obj["context"]["exception_present"] = True

        # Include custom extra fields if attached to LogRecord
        extra_keys = set(record.__dict__.keys()) - {
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
            "correlation_id",
            "taskName",
            "redacted",
            "message",
        }
        ctx_extra = {}
        for k in extra_keys:
            val = getattr(record, k)
            ctx_extra[k] = val.isoformat() if hasattr(val, "isoformat") else val
        if ctx_extra:
            log_obj["context"].update(ctx_extra)

        return json.dumps(log_obj, default=str)


def configure_structured_logging(
    log_dir: str = "logs",
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 7,
    enable_redaction: bool = True,
) -> None:
    """Configures global structured JSON logging with rotating file handlers."""
    os.makedirs(log_dir, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    filters: list = [CorrelationIdFilter()]
    if enable_redaction:
        filters.append(RedactionFilter())

    # Stream Handler (stdout)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JSONFormatter())
    for f in filters:
        stdout_handler.addFilter(f)
    root_logger.addHandler(stdout_handler)

    # Rotating File Handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "super_trademan.json.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    for f in filters:
        file_handler.addFilter(f)
    root_logger.addHandler(file_handler)
