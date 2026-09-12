"""Tests for Health Check Service (observability truthfulness lane SUB-10)."""

import os
import time

import pytest

from trading.config import ExecutionMode
from trading.data.staleness import StalenessSentinel
from trading.execution.venue_adapter import MockVenueAdapter
from trading.ops.health_service import HealthService, HealthStatus


def test_health_service_healthy():
    venue = MockVenueAdapter()
    sentinel = StalenessSentinel()
    # Record tick to make sentinel fresh
    sentinel.record_tick("BTC", timestamp_ms=time.time() * 1000.0)

    service = HealthService(venue_adapter=venue, staleness_sentinel=sentinel)
    aggregate, components, resp = service.evaluate_system_health("BTC")

    assert aggregate == HealthStatus.HEALTHY
    assert resp["status"] == HealthStatus.HEALTHY
    assert len(components) == 4


def test_health_service_degraded_stale_data():
    service = HealthService(venue_adapter=MockVenueAdapter())
    # Symbol "BTC" not in sentinel -> stale -> DEGRADED
    aggregate, components, resp = service.evaluate_system_health("BTC")

    assert aggregate == HealthStatus.DEGRADED
    assert resp["status"] == HealthStatus.DEGRADED


# --------------------------------------------------------------------------
# UNKNOWN-not-HEALTHY: no fabricated statuses when a probe is impossible.
# --------------------------------------------------------------------------


def test_database_health_unknown_when_no_postgres_url(monkeypatch):
    """No POSTGRES_URL => UNKNOWN. Never the old hardcoded HEALTHY/1.2ms."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)

    service = HealthService(venue_adapter=MockVenueAdapter(), postgres_url="")
    health = service.check_database_health()

    assert health.status == HealthStatus.UNKNOWN
    assert health.status != HealthStatus.HEALTHY
    assert "POSTGRES_URL" in health.details
    # The legacy fake value must be gone.
    assert health.latency_ms != pytest.approx(1.2)


def test_database_health_real_probe_reports_measured_latency(monkeypatch):
    """With POSTGRES_URL set, a REAL asyncpg SELECT 1 runs and its measured latency is reported."""
    calls = {}

    class FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetchval(self, query):
            calls["query"] = query
            await __import__("asyncio").sleep(0.001)  # measurable delay
            return 1

        async def close(self):
            calls["closed"] = True

    import asyncpg

    async def fake_connect(dsn, timeout=None):
        calls["dsn"] = dsn
        return FakeConn()

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://probe:secret@db.local:5432/trade")

    service = HealthService(
        venue_adapter=MockVenueAdapter(),
        postgres_url="postgresql://probe:secret@db.local:5432/trade",
    )
    health = service.check_database_health()

    assert calls["dsn"] == "postgresql://probe:secret@db.local:5432/trade"
    assert calls["query"] == "SELECT 1"
    assert calls.get("closed") is True
    assert health.status == HealthStatus.HEALTHY
    assert health.latency_ms > 0.0  # actually measured, not hardcoded


def test_venue_adapter_required_outside_simulation(monkeypatch):
    """Refusing a silent MockVenueAdapter fallback is enforced outside simulation."""
    # No execution_mode given -> defaults to non-simulation (BACKTEST gate applies
    # only when explicitly simulation; None means caller must supply an adapter).
    with pytest.raises(ValueError, match="venue_adapter"):
        HealthService(venue_adapter=None)  # type: ignore[arg-type]


def test_mock_adapter_gated_behind_simulation_mode(monkeypatch):
    """MockVenueAdapter auto-selection ONLY happens in simulation mode."""

    class SimMode:
        SIMULATION = ExecutionMode.BACKTEST  # alias used by the gating helper

    sentinel = StalenessSentinel()
    svc_sim = HealthService(
        venue_adapter=None,  # type: ignore[arg-type]
        staleness_sentinel=sentinel,
        execution_mode=ExecutionMode.BACKTEST,
    )
    assert isinstance(svc_sim.venue_adapter, MockVenueAdapter)


def test_exchange_api_labels_simulation_mock_honestly():
    """A mock venue reports as simulation, never as live exchange truth."""
    service = HealthService(venue_adapter=MockVenueAdapter())
    health = service.check_exchange_api_health()
    assert health.status == HealthStatus.HEALTHY
    assert "MockVenueAdapter" in health.details or "Simulation" in health.details


def _write_state_file(tmp_path, age_seconds):
    path = tmp_path / "tier_state.json"
    path.write_text('{"tier": "normal"}', encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return str(path)


def test_daemon_health_unknown_without_state_path(monkeypatch):
    monkeypatch.delenv("RISK_TIER_STATE_FILE", raising=False)
    service = HealthService(venue_adapter=MockVenueAdapter(), heartbeat_state_path="")
    health = service.check_daemon_health()
    assert health.status == HealthStatus.UNKNOWN
    assert health.status != HealthStatus.HEALTHY


def test_daemon_health_missing_state_file_is_unknown(tmp_path, monkeypatch):
    monkeypatch.delenv("RISK_TIER_STATE_FILE", raising=False)
    service = HealthService(
        venue_adapter=MockVenueAdapter(),
        heartbeat_state_path=str(tmp_path / "does_not_exist.json"),
    )
    health = service.check_daemon_health()
    assert health.status == HealthStatus.UNKNOWN


def test_daemon_health_fresh_heartbeat_is_healthy(tmp_path):
    path = _write_state_file(tmp_path, age_seconds=5.0)
    service = HealthService(
        venue_adapter=MockVenueAdapter(),
        heartbeat_state_path=path,
        heartbeat_interval_seconds=60.0,
    )
    health = service.check_daemon_health()
    assert health.status == HealthStatus.HEALTHY  # 5s < 2*60s budget


def test_daemon_health_stale_heartbeat_is_critical(tmp_path):
    path = _write_state_file(tmp_path, age_seconds=3 * 60.0)
    service = HealthService(
        venue_adapter=MockVenueAdapter(),
        heartbeat_state_path=path,
        heartbeat_interval_seconds=60.0,
    )
    health = service.check_daemon_health()
    assert health.status == HealthStatus.CRITICAL  # 180s >= 120s budget
