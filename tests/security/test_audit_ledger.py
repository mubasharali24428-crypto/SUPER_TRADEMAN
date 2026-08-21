"""Tests for Immutable Cryptographic Audit Ledger."""

import pytest

from trading.security.audit_ledger import AuditLedger, GENESIS_HASH


def test_audit_ledger_chain_and_tamper_detection():
    ledger = AuditLedger()
    assert ledger.verify_chain_integrity()

    # Append 3 events
    rec1 = ledger.append_event("MODE_PROMOTION", {"from": "PAPER", "to": "SHADOW"})
    rec2 = ledger.append_event("KILL_SWITCH_TRIAGED", {"reason": "drawdown"})
    rec3 = ledger.append_event("ISSUER_TOKEN_ORDER", {"asset": "BTC", "size": 1.0})

    assert len(ledger.chain) == 3
    assert rec1.prev_hash == GENESIS_HASH
    assert rec2.prev_hash == rec1.hash
    assert rec3.prev_hash == rec2.hash
    assert ledger.verify_chain_integrity()

    # Tamper with record 2 payload -> verify_chain_integrity should detect tampering
    ledger.chain[1] = type(rec2)(
        record_id=rec2.record_id,
        timestamp_utc=rec2.timestamp_utc,
        event_type=rec2.event_type,
        payload_json='{"tampered": true}',
        prev_hash=rec2.prev_hash,
        hash=rec2.hash,
    )
    assert not ledger.verify_chain_integrity()
