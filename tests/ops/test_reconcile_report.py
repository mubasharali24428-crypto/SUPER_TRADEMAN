"""Tests for Reconciliation Report Generator."""

from scripts.reconcile_report import generate_reconciliation_report
from trading.config import ExecutionMode
from trading.execution.venue_adapter import MockVenueAdapter


def test_reconciliation_report_clean():
    adapter = MockVenueAdapter()
    report = generate_reconciliation_report(venue_adapter=adapter, execution_mode=ExecutionMode.SHADOW)

    assert report.status == "CLEAN"
    assert report.positions_match
    assert report.balances_match
    assert report.open_orders_match
    assert report.quarantine_count == 0
