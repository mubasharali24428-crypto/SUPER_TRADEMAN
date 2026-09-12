"""Tests for Deployment Preflight Checker (HK-1 fail-closed alignment)."""

import os

import pytest

from scripts.preflight_check import PreflightCheckResult, run_preflight_checks
from trading.config import ExecutionMode


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate each test from the caller's environment."""
    for var in (
        "POSTGRES_URL",
        "DATABASE_URL",
        "REDIS_URL",
        "RISK_PCT",
        "EXCHANGE_API_KEY",
        "EXCHANGE_API_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def ok_db_prober():
    calls = []

    def _prober(url):
        calls.append(url)
        return True, "Connected; Alembic-owned tables present."

    _prober.calls = calls
    return _prober


def test_preflight_passes_with_secrets_and_healthy_db(monkeypatch, ok_db_prober):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/trading")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    passed, results = run_preflight_checks(ExecutionMode.SHADOW, db_prober=ok_db_prober)
    assert passed, [r.details for r in results if not r.passed]
    assert all(r.passed for r in results if r.severity == "BLOCKING")
    assert len(results) >= 8
    # DB probe ran through the resolve_postgres_url-resolved DSN.
    assert ok_db_prober.calls == ["postgresql://user:pw@localhost:5432/trading"]


def test_missing_postgres_url_fails_loudly(ok_db_prober):
    passed, results = run_preflight_checks(ExecutionMode.SHADOW, db_prober=ok_db_prober)
    assert not passed
    failed = {r.name: r for r in results if not r.passed}
    assert any("POSTGRES_URL" in name for name in failed), list(failed)
    # No URL -> the DB probe must be skipped, not faked.
    assert "Database: Connectivity & Alembic Schema Present" in failed
    assert ok_db_prober.calls == []


def test_missing_redis_url_fails_loudly(monkeypatch, ok_db_prober):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/trading")
    passed, results = run_preflight_checks(ExecutionMode.SHADOW, db_prober=ok_db_prober)
    assert not passed
    failed = {r.name for r in results if not r.passed}
    assert any("REDIS_URL" in name for name in failed), failed


def test_unreachable_database_fails_with_migration_guidance(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/trading")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    def failing_prober(url):
        return False, (
            "Connected, but Alembic-managed table(s) missing: alembic_version, "
            "funding_rates, ohlcv. Apply migrations: POSTGRES_URL=<url> alembic upgrade head."
        )

    passed, results = run_preflight_checks(
        ExecutionMode.SHADOW, db_prober=failing_prober
    )
    assert not passed
    db_result = next(r for r in results if r.name.startswith("Database:"))
    assert not db_result.passed
    assert "alembic upgrade head" in db_result.details


def test_placeholder_credential_rejected(monkeypatch, ok_db_prober):
    masked = "postgresql://" + "user:" + "***@" + "localhost:5432/trading"
    monkeypatch.setenv("POSTGRES_URL", masked)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    passed, results = run_preflight_checks(ExecutionMode.SHADOW, db_prober=ok_db_prober)
    assert not passed
    failed = {r.name for r in results if not r.passed}
    assert any("Placeholder" in name for name in failed), failed


def test_live_mode_requires_exchange_credentials(monkeypatch, ok_db_prober):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/trading")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    passed, results = run_preflight_checks(
        ExecutionMode.LIVE_RESTRICTED, db_prober=ok_db_prober
    )
    assert not passed
    failed = {r.name for r in results if not r.passed}
    assert any("Exchange API Credentials" in name for name in failed), failed


def test_risk_cap_enforced_via_env_contract(monkeypatch, ok_db_prober):
    """RISK_PCT above the 0.02 sovereign cap must block (existing behavior)."""
    from unittest.mock import patch

    real_getenv = os.getenv

    def getenv(key, default=None):
        if key == "RISK_PCT":
            return "0.05"
        return real_getenv(key, default)

    with patch("scripts.preflight_check.os.getenv", getenv):
        passed, results = run_preflight_checks(
            ExecutionMode.SHADOW, db_prober=ok_db_prober
        )
    assert not passed
    failed = {r.name for r in results if not r.passed}
    assert any("Risk Percentage" in name for name in failed), failed


def test_result_dataclass_shape():
    r = PreflightCheckResult(name="x", severity="BLOCKING", passed=True, details="ok")
    assert r.severity in ("BLOCKING", "WARNING")


# Legacy test kept green under the new contract: invalid risk blocks promotion.
def test_preflight_checks_fail_invalid_risk(monkeypatch, ok_db_prober):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/trading")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("RISK_PCT", "0.05")
    passed, results = run_preflight_checks(ExecutionMode.SHADOW, db_prober=ok_db_prober)
    assert not passed
