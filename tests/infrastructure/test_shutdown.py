"""Tests for Graceful Signal Shutdown Handler."""

import pytest

from trading.infrastructure.ha_lock import ActivePassiveManager
from trading.infrastructure.shutdown import GracefulShutdownHandler
from trading.security.audit_ledger import AuditLedger


def test_graceful_shutdown_handler():
    ha_mgr = ActivePassiveManager(node_id="node_1")
    ha_mgr.acquire_lock()
    ledger = AuditLedger()

    handler = GracefulShutdownHandler(ha_manager=ha_mgr, audit_ledger=ledger)

    # Simulate receiving SIGTERM (signal 15)
    handler.handle_signal(15)

    assert handler.shutdown_initiated
    assert not ha_mgr.is_active  # Lock released
    assert len(ledger.chain) == 1
    assert ledger.chain[0].event_type == "GRACEFUL_SHUTDOWN"
