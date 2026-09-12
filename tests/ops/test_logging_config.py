"""Tests for Structured JSON Logging & Correlation IDs."""

import json
import logging

import pytest

from trading.ops.logging_config import (JSONFormatter, RedactionFilter,
                                        _redact_text, get_correlation_id,
                                        set_correlation_id)


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
    assert data["severity"] == "INFO"
    assert data["trace_id"] == "test_corr_123"
    assert data["service"] == "super_trademan"
    assert data["logger"] == "test_logger"
    assert "ts" in data
    assert "context" in data


# --------------------------------------------------------------------------
# Correlation IDs are context-local (contextvars), not process-global.
# --------------------------------------------------------------------------


def test_set_correlation_id_is_context_local():
    set_correlation_id("ctx-A")
    assert get_correlation_id() == "ctx-A"

    import asyncio

    async def other_context():
        # A separate task gets its own context; it does not see ctx-A.
        seen = get_correlation_id()
        set_correlation_id("ctx-B")
        return seen, get_correlation_id()

    async def main():
        return await asyncio.gather(other_context())

    ((inner_seen, inner_after),) = __import__("asyncio").run(main())
    # The task did NOT inherit ctx-A's value as its own mutation target...
    assert inner_after == "ctx-B"
    # ...and the parent context still holds ctx-A afterwards.
    assert get_correlation_id() == "ctx-A"


def test_correlation_id_filter_stamps_record():
    from trading.ops.logging_config import CorrelationIdFilter

    set_correlation_id("filter-cid")
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="t.py",
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    assert CorrelationIdFilter().filter(record) is True
    assert getattr(record, "correlation_id") == "filter-cid"


# --------------------------------------------------------------------------
# Redaction: postgres DSNs / querystrings never survive into log output.
# --------------------------------------------------------------------------


def test_redact_text_strips_dsn_credentials_and_querystring():
    raw = "postgres://trader:s3cr3t@db.internal:5432/tradedb?sslmode=require&application_name=st"
    red = _redact_text(raw)
    assert "s3cr3t" not in red
    assert "db.internal" not in red
    assert "sslmode" not in red
    assert red.startswith("postgres://")


def test_redaction_filter_scrubs_logged_exception():
    filt = RedactionFilter()
    formatter = JSONFormatter()

    dsn = "postgresql://svc:hunter2@prod-db:5432/market?connect_timeout=10"
    try:
        raise RuntimeError(f"connection failed for {dsn}")
    except RuntimeError:
        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname="t.py",
            lineno=1,
            msg="DB probe failed",
            args=(),
            exc_info=(
                RuntimeError,
                __import__("sys").exc_info()[1],
                None,
            ),
        )
        assert filt.filter(record) is True

    out = formatter.format(record)
    data = json.loads(out)

    assert "hunter2" not in json.dumps(data)
    assert "prod-db" not in json.dumps(data)
    assert "connect_timeout" not in json.dumps(data)
    assert "postgres" in data["message"] or "postgres" in data.get("exception", "")


def test_redaction_filter_scrubs_message_args():
    filt = RedactionFilter()
    record = logging.LogRecord(
        name="t",
        level=logging.WARNING,
        pathname="t.py",
        lineno=1,
        msg="retrying %s",
        args=("postgres://u:p@h:5432/d",),
        exc_info=None,
    )
    filt.filter(record)
    formatted = JSONFormatter().format(record)
    assert ":p@" not in formatted
