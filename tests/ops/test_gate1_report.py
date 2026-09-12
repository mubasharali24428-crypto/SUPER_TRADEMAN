"""Tests for the Gate 1 report generator (fail-closed, persisted-data-only).

Covers audit finding F-0007/G-027: the generator must never render a promotion
report from fabricated/hardcoded records and must refuse without >= 20 real
persisted daily records.
"""

import pytest

from scripts import generate_gate1_report as mod
from scripts.generate_gate1_report import (EXIT_EVAL_FAIL,
                                           EXIT_INSUFFICIENT_DATA,
                                           EXIT_REPORT_FAIL, EXIT_REPORT_PASS,
                                           InsufficientDataError,
                                           generate_gate1_markdown_report)
from trading.ops.deployment_metrics import (DeploymentMetricRecord,
                                            DeploymentMetricsStore)


def _make_record(day: int, pnl_pct: float = 0.003) -> DeploymentMetricRecord:
    """One real-looking persisted daily record (20 days * 0.3% = 6% cumulative)."""
    month = 1 + (day - 1) // 28
    dom = (day - 1) % 28 + 1
    return DeploymentMetricRecord(
        metric_date=f"2026-{month:02d}-{dom:02d}",
        execution_mode="shadow",
        symbols="BTC/USDT",
        signals_generated=25,
        signals_approved=23,
        shadow_fills_generated=23,
        avg_signal_to_fill_latency_ms=42.0,
        p99_signal_to_fill_latency_ms=180.0,
        avg_shadow_slippage_bps=3.5,
        shadow_pnl_pct=pnl_pct,
    )


def _seed_store(days: int, pnl_pct: float = 0.003) -> DeploymentMetricsStore:
    store = DeploymentMetricsStore()
    for day in range(1, days + 1):
        store.record_metrics(_make_record(day, pnl_pct))
    return store


def test_empty_store_raises_insufficient_data():
    store = DeploymentMetricsStore()
    with pytest.raises(InsufficientDataError):
        generate_gate1_markdown_report(days=30, store=store)


def test_store_below_20_days_is_refused():
    for n in (1, 5, 19):
        with pytest.raises(InsufficientDataError):
            generate_gate1_markdown_report(days=30, store=_seed_store(n))


def test_no_fabricated_records_injected():
    """The generator must not write any record into the store it reads."""
    store = DeploymentMetricsStore()
    before = len(store.metrics_history)
    with pytest.raises(InsufficientDataError):
        generate_gate1_markdown_report(days=30, store=store)
    assert len(store.metrics_history) == before == 0


def test_report_renders_from_twenty_real_records():
    store = _seed_store(20)
    md_report = generate_gate1_markdown_report(days=20, store=store)
    assert "# Gate 1 Validation & Mode Promotion Report" in md_report
    assert "SHA256:" in md_report
    assert "Executive Summary:" in md_report
    assert "Persisted Daily Records:** 20" in md_report
    assert "GATE_1_PASS" in md_report  # 20 clean days at 0.3%/day passes Gate 1


def test_cli_exit_2_on_empty_store(monkeypatch, capsys):
    """Empty persisted store => CLI refuses to render and exits 2."""
    monkeypatch.setattr(mod, "DeploymentMetricsStore", lambda: _seed_store(0))
    monkeypatch.setattr("sys.argv", ["generate_gate1_report.py", "--days", "30"])
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert excinfo.value.code == EXIT_INSUFFICIENT_DATA == 2
    out = capsys.readouterr().out
    assert "INSUFFICIENT_DATA" in out
    assert "NOT_RENDERED" in out


def test_cli_exit_0_on_full_passing_campaign(monkeypatch, capsys):
    monkeypatch.setattr(mod, "DeploymentMetricsStore", lambda: _seed_store(20))
    monkeypatch.setattr("sys.argv", ["generate_gate1_report.py", "--days", "20"])
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert excinfo.value.code == EXIT_REPORT_PASS == 0
    out = capsys.readouterr().out
    assert "# Gate 1 Validation & Mode Promotion Report" in out


def test_exit_code_constants_contract():
    assert EXIT_REPORT_PASS == 0
    assert EXIT_INSUFFICIENT_DATA == 2
    assert EXIT_EVAL_FAIL == 3
    # Report-rendered-but-gate-failed is distinct from eval error.
    assert EXIT_REPORT_FAIL != EXIT_EVAL_FAIL
