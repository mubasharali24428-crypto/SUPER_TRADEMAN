"""Tests for Shadow Mode Validation Reporter & Gate 1 Engine.

Covers audit finding F-0044/G-026: the reporter must never seed or synthesize
metrics, must generate strictly from the persisted store, and must exit
non-zero when the store is empty or has fewer than 20 persisted days.
"""

import pytest

from scripts import shadow_report
from scripts.shadow_report import MIN_DAYS_REQUIRED, evaluate_gate_1
from trading.ops.deployment_metrics import (DeploymentMetricRecord,
                                            DeploymentMetricsStore)


def _make_record(day: int, pnl_pct: float = 0.0025) -> DeploymentMetricRecord:
    month = 1 + (day - 1) // 28
    dom = (day - 1) % 28 + 1
    return DeploymentMetricRecord(
        metric_date=f"2026-{month:02d}-{dom:02d}",
        execution_mode="shadow",
        symbols="BTC/USDT",
        signals_generated=42,
        signals_approved=38,
        shadow_fills_generated=38,
        liquidity_deficit_pct=0.01,
        staleness_circuit_breaker_trips=0,
        avg_signal_to_fill_latency_ms=45.0,
        p95_signal_to_fill_latency_ms=120.0,
        p99_signal_to_fill_latency_ms=210.0,
        shadow_pnl_pct=pnl_pct,
    )


def _seed_store(days: int, pnl_pct: float = 0.0025) -> DeploymentMetricsStore:
    store = DeploymentMetricsStore()
    for day in range(1, days + 1):
        store.record_metrics(_make_record(day, pnl_pct))
    return store


def test_evaluate_gate_1_pass():
    record = DeploymentMetricRecord(
        metric_date="2026-08-18",
        execution_mode="shadow",
        symbols="BTC/USDT",
        shadow_pnl_pct=0.05,
        staleness_circuit_breaker_trips=2,
        liquidity_deficit_pct=0.01,
        reconciliation_mismatches=0,
    )
    passed, failures, details = evaluate_gate_1(
        record, backtest_expected_pnl_pct=0.05, backtest_pnl_std_dev=0.02
    )
    assert passed
    assert len(failures) == 0
    assert details["shadow_pnl_z_score"] == 0.0


def test_evaluate_gate_1_fail_missing_benchmark():
    record = DeploymentMetricRecord(
        metric_date="2026-08-18", execution_mode="shadow", symbols="BTC/USDT"
    )
    passed, failures, details = evaluate_gate_1(record, backtest_expected_pnl_pct=None)
    assert not passed
    assert any("MISSING_BACKTEST_BENCHMARK" in f for f in failures)


def test_main_never_calls_record_metrics():
    """main() bytecode must not reference any store-write entrypoint (F-0044)."""
    assert "record_metrics" not in shadow_report.main.__code__.co_names


def _run_main(monkeypatch, store_factory, extra_args=None):
    """Run shadow_report.main() against an injected store factory; capture stores."""
    created = []

    def factory():
        s = store_factory()
        created.append(s)
        return s

    monkeypatch.setattr(shadow_report, "DeploymentMetricsStore", factory)
    argv = ["shadow_report.py", "--days", "20"] + (extra_args or [])
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        shadow_report.main()
    return excinfo.value.code, created


def test_empty_store_exits_nonzero_and_stays_empty(monkeypatch):
    """Empty store => exit 2 and NO synthetic record is injected into the store."""
    code, created = _run_main(monkeypatch, lambda: DeploymentMetricsStore())
    assert code == 2
    assert len(created) == 1
    assert created[0].metrics_history == []  # nothing was self-seeded


def test_insufficient_store_below_20_days_exits_2(monkeypatch):
    code, created = _run_main(monkeypatch, lambda: _seed_store(19))
    assert code == 2


def test_full_window_clean_campaign_exits_0(monkeypatch, capsys):
    # 20 days * 2.5%/day = 5% cumulative => z-score 0 vs 5% benchmark => PASS.
    code, _created = _run_main(monkeypatch, lambda: _seed_store(20, pnl_pct=0.0025))
    assert code == 0
    assert "GATE 1 STATUS    : PASS" in capsys.readouterr().out


def test_full_window_broken_campaign_exits_3(monkeypatch, capsys):
    # Cumulative PnL far outside benchmark band => gate FAIL => exit 3.
    code, _created = _run_main(monkeypatch, lambda: _seed_store(20, pnl_pct=0.01))
    assert code == 3
    assert "GATE 1 STATUS    : FAIL" in capsys.readouterr().out


def test_json_output_reports_not_evaluable_on_empty_store(monkeypatch, capsys):
    import json as _json

    code, _created = _run_main(
        monkeypatch, lambda: DeploymentMetricsStore(), extra_args=["--format", "json"]
    )
    assert code == 2
    payload = _json.loads(capsys.readouterr().out)
    assert payload["gate_1_status"] == "NOT_EVALUABLE"
    assert payload["reason"] == "INSUFFICIENT_DATA"


def test_min_days_constant_is_20():
    assert MIN_DAYS_REQUIRED == 20
