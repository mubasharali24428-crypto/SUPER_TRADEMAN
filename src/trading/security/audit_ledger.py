"""Cryptographically Chained Immutable Audit Ledger for Production Hardening."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading.observability.logger import get_logger

__all__ = [
    "AuditRecord",
    "AuditLedger",
]

logger = get_logger("trading.security.audit_ledger")

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


@dataclass
class AuditRecord:
    record_id: str
    timestamp_utc: str
    event_type: str
    payload_json: str
    prev_hash: str
    hash: str


class AuditLedger:
    """Append-only SHA-256 cryptographically chained audit log store."""

    def __init__(self):
        self.chain: List[AuditRecord] = []

    def _compute_hash(self, prev_hash: str, timestamp_utc: str, event_type: str, payload_json: str) -> str:
        data_str = f"{prev_hash}|{timestamp_utc}|{event_type}|{payload_json}"
        return hashlib.sha256(data_str.encode("utf-8")).hexdigest()

    def append_event(self, event_type: str, payload: Dict[str, Any]) -> AuditRecord:
        """Appends a new event to the audit ledger, computing SHA-256 chain hash."""
        prev_hash = self.chain[-1].hash if self.chain else GENESIS_HASH
        timestamp_utc = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, sort_keys=True)

        rec_hash = self._compute_hash(prev_hash, timestamp_utc, event_type, payload_json)
        record = AuditRecord(
            record_id=f"aud_{uuid.uuid4().hex[:12]}",
            timestamp_utc=timestamp_utc,
            event_type=event_type,
            payload_json=payload_json,
            prev_hash=prev_hash,
            hash=rec_hash,
        )

        self.chain.append(record)
        logger.info(f"[AUDIT_EVENT_APPENDED] Type={event_type}, Hash={rec_hash[:12]}...")
        return record

    def verify_chain_integrity(self) -> bool:
        """Verifies the SHA-256 cryptographic chain integrity across all records."""
        if not self.chain:
            return True

        for i in range(len(self.chain)):
            record = self.chain[i]
            expected_prev_hash = self.chain[i - 1].hash if i > 0 else GENESIS_HASH

            if record.prev_hash != expected_prev_hash:
                logger.error(f"[AUDIT_CORRUPTION] Invalid prev_hash at index {i}: expected {expected_prev_hash}, got {record.prev_hash}")
                return False

            recalculated_hash = self._compute_hash(record.prev_hash, record.timestamp_utc, record.event_type, record.payload_json)
            if record.hash != recalculated_hash:
                logger.error(f"[AUDIT_CORRUPTION] Hash mismatch at index {i}: record {record.hash}, recalculated {recalculated_hash}")
                return False

        return True
