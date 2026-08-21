"""Graceful Shutdown Handler for OS Signals (SIGTERM, SIGINT)."""

import signal
import sys
from typing import Optional

from trading.infrastructure.ha_lock import ActivePassiveManager
from trading.observability.logger import get_logger
from trading.security.audit_ledger import AuditLedger

__all__ = ["GracefulShutdownHandler"]

logger = get_logger("trading.infrastructure.shutdown")


class GracefulShutdownHandler:
    """Orchestrates graceful signal shutdown, releasing HA locks and recording audit events."""

    def __init__(
        self,
        ha_manager: Optional[ActivePassiveManager] = None,
        audit_ledger: Optional[AuditLedger] = None,
    ):
        self.ha_manager = ha_manager
        self.audit_ledger = audit_ledger
        self.shutdown_initiated = False

    def handle_signal(self, signum: int, frame: Optional[object] = None) -> None:
        """Handles SIGTERM / SIGINT signals cleanly."""
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.warning(f"[GRACEFUL_SHUTDOWN_INITIATED] Received OS signal {sig_name}. Halting new order signals.")

        self.shutdown_initiated = True

        # Release HA Primary Lock cleanly
        if self.ha_manager is not None:
            self.ha_manager.release_lock()

        # Log GRACEFUL_SHUTDOWN event in Audit Ledger
        if self.audit_ledger is not None:
            self.audit_ledger.append_event("GRACEFUL_SHUTDOWN", {"signal": sig_name, "status": "CLEAN_RELEASE"})

        logger.info("[GRACEFUL_SHUTDOWN_COMPLETED] System shutdown sequence finished cleanly.")

    def register_signal_handlers(self) -> None:
        """Registers handlers for SIGTERM and SIGINT."""
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
