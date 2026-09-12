"""Tests for Deployment Metrics Store: memory cache + Postgres upserts + degradation."""

import asyncio
from datetime import datetime, timezone

import pytest

from trading.ops.deployment_metrics import (UPSERT_DEPLOYMENT_METRICS_SQL,
                                            AlertRecord,
                                            DeploymentMetricRecord,
                                            DeploymentMetricsStore,
                                            DrillResultRecord,
                                            ReconciliationReportRecord)


class FakePool:
    """Minimal asyncpg.Pool stand-in recording executed statements."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        if self.fail:
            raise ConnectionError("postgres unavailable")
        self.executed.append(("execute", sql, args))
        return "OK"


def _record(metric_date="2026-08-17", signals=10, **kw):
    return DeploymentMetricRecord(
        metric_date=metric_date,
        execution_mode="shadow",
        symbols="BTC/USDT",
        signals_generated=signals,
        **kw,
    )


def test_deployment_metrics_store():
    store = DeploymentMetricsStore()

    rec1 = _record("2026-08-17", signals=10, signals_approved=8, shadow_pnl_pct=0.02)
    rec2 = _record("2026-08-18", signals=15, signals_approved=12, shadow_pnl_pct=0.03)

    store.record_metrics(rec1)
    store.record_metrics(rec2)

    cum = store.get_cumulative_metrics(days=2)
    assert cum is not None
    assert cum.signals_generated == 25
    assert cum.signals_approved == 20
    assert cum.shadow_pnl_pct == pytest.approx(0.05)


def test_drill_and_reconciliation_records():
    store = DeploymentMetricsStore()

    drill_rec = DrillResultRecord(
        drill_name="stale_data", execution_mode="shadow", status="PASS"
    )
    store.record_drill_result(drill_rec)
    assert len(store.drill_history) == 1

    rec_report = ReconciliationReportRecord(
        reconciliation_id="rec_1",
        timestamp_utc=datetime.now(timezone.utc),
        execution_mode="shadow",
        status="CLEAN",
    )
    store.record_reconciliation_report(rec_report)
    assert len(store.reconciliation_history) == 1


def test_memory_list_is_read_cache_and_upsert_sql_targets_daily_key():
    pool = FakePool()
    store = DeploymentMetricsStore(pool=pool)

    store.record_metrics(_record(signals=7))
    # Memory read-cache still updated synchronously.
    assert len(store.metrics_history) == 1
    # Upsert keyed by the table's UNIQUE (metric_date, execution_mode, symbols).
    assert (
        "ON CONFLICT (metric_date, execution_mode, symbols) DO UPDATE"
        in UPSERT_DEPLOYMENT_METRICS_SQL
    )


async def test_record_metrics_upserts_row_via_pool():
    pool = FakePool()
    store = DeploymentMetricsStore(pool=pool)

    store.record_metrics(_record())
    await asyncio.sleep(0)  # let the fire-and-forget task run

    kind, sql, args = pool.executed[0]
    assert kind == "execute"
    assert sql.startswith("INSERT INTO deployment_metrics")
    assert "ON CONFLICT (metric_date, execution_mode, symbols) DO UPDATE" in sql
    # Key columns lead the positional args.
    assert args[0] == "2026-08-17"
    assert args[1] == "shadow"


async def test_graceful_degradation_when_db_unavailable(caplog):
    from trading.observability.logger import get_logger

    store = DeploymentMetricsStore(pool=FakePool(fail=True))

    # Must NOT raise; stays memory-only.
    store.record_metrics(_record())
    await asyncio.sleep(0)

    assert len(store.metrics_history) == 1  # memory intact
    assert "deployment_metrics.upsert" in store._db_degraded

    store.record_drill_result(
        DrillResultRecord(drill_name="d", execution_mode="shadow", status="PASS")
    )
    await asyncio.sleep(0)
    assert len(store.drill_history) == 1


async def test_drill_and_reconciliation_persist_via_pool():
    pool = FakePool()
    store = DeploymentMetricsStore(pool=pool)

    store.record_drill_result(
        DrillResultRecord(
            drill_name="kill_switch", execution_mode="shadow", status="PASS"
        )
    )
    store.record_reconciliation_report(
        ReconciliationReportRecord(
            reconciliation_id="rec_9",
            timestamp_utc=datetime.now(timezone.utc),
            execution_mode="shadow",
        )
    )
    await asyncio.sleep(0)

    kinds = [k for k, _, _ in pool.executed]
    sqls = [sql for _, sql, _ in pool.executed]
    assert kinds == ["execute", "execute"]
    assert "INSERT INTO drill_results" in sqls[0]
    assert "INSERT INTO reconciliation_reports" in sqls[1]


async def test_persist_alert_inserts_alert_row():
    from trading.ops.deployment_metrics import persist_alert

    pool = FakePool()
    alert = AlertRecord(
        alert_id="a-1", alert_name="n", severity="HIGH", message="m", channel="slack"
    )
    await persist_alert(pool, alert)

    _, sql, args = pool.executed[0]
    assert "INSERT INTO alerts" in sql
    assert args[0] == "a-1"
