"""Structured JSON Logging Infrastructure with Correlation IDs and Rotation Rules."""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

__all__ = [
    "JSONFormatter",
    "get_correlation_id",
    "set_correlation_id",
    "configure_structured_logging",
]

_CORRELATION_ID = ""


def get_correlation_id() -> str:
    global _CORRELATION_ID
    if not _CORRELATION_ID:
        _CORRELATION_ID = uuid.uuid4().hex[:12]
    return _CORRELATION_ID


def set_correlation_id(correlation_id: str) -> None:
    global _CORRELATION_ID
    _CORRELATION_ID = correlation_id


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON lines with UTC timestamps and correlation IDs."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "correlation_id": getattr(record, "correlation_id", get_correlation_id()),
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Include custom extra fields if attached to LogRecord
        extra_keys = set(record.__dict__.keys()) - {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "correlation_id"
        }
        for k in extra_keys:
            log_obj[k] = getattr(record, k)

        return json.dumps(log_obj)


def configure_structured_logging(
    log_dir: str = "logs",
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 7,
) -> None:
    """Configures global structured JSON logging with rotating file handlers."""
    os.makedirs(log_dir, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Stream Handler (stdout)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(stdout_handler)

    # Rotating File Handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "super_trademan.json.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)
