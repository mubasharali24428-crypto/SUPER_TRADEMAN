"""Tests for Structured JSON Logging & Correlation IDs."""

import json
import logging
import pytest

from trading.ops.logging_config import JSONFormatter, get_correlation_id, set_correlation_id


def test_json_formatter_outputs_valid_json():
    formatter = JSONFormatter()
    set_correlation_id("test_corr_123")

    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test log message",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["message"] == "Test log message"
    assert data["level"] == "INFO"
    assert data["correlation_id"] == "test_corr_123"
    assert "timestamp_utc" in data
