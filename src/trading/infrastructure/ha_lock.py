"""Active-Passive Distributed Lock & High Availability Heartbeat Manager."""

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from trading.observability.logger import get_logger

__all__ = ["ActivePassiveManager"]

logger = get_logger("trading.infrastructure.ha_lock")


@dataclass
class LockState:
    node_id: str
    last_heartbeat_ts: float
    ttl_sec: float = 15.0


class ActivePassiveManager:
    """Manages active-passive failover distributed lock with TTL expiration."""

    def __init__(self, node_id: str, ttl_sec: float = 15.0):
        self.node_id = node_id
        self.ttl_sec = ttl_sec
        self.is_active = False
        self._global_lock_state: Optional[LockState] = None

    def acquire_lock(self, current_time: Optional[float] = None) -> bool:
        """Attempts to acquire primary active node lock."""
        now = current_time if current_time is not None else time.time()

        if self._global_lock_state is None:
            self._global_lock_state = LockState(node_id=self.node_id, last_heartbeat_ts=now, ttl_sec=self.ttl_sec)
            self.is_active = True
            logger.info(f"[HA_LOCK_ACQUIRED] Node {self.node_id} acquired primary active lock.")
            return True

        if self._global_lock_state.node_id == self.node_id:
            self._global_lock_state.last_heartbeat_ts = now
            self.is_active = True
            return True

        # Check if current holder lock has expired
        elapsed = now - self._global_lock_state.last_heartbeat_ts
        if elapsed > self._global_lock_state.ttl_sec:
            logger.warning(
                f"[HA_FAILOVER_TRIGGERED] Primary node {self._global_lock_state.node_id} lock expired "
                f"(age {elapsed:.1f}s > {self.ttl_sec}s). Node {self.node_id} taking over primary lock."
            )
            self._global_lock_state = LockState(node_id=self.node_id, last_heartbeat_ts=now, ttl_sec=self.ttl_sec)
            self.is_active = True
            return True

        self.is_active = False
        return False

    def send_heartbeat(self, current_time: Optional[float] = None) -> bool:
        """Refreshes primary node heartbeat timestamp."""
        now = current_time if current_time is not None else time.time()
        if self.is_active and self._global_lock_state and self._global_lock_state.node_id == self.node_id:
            self._global_lock_state.last_heartbeat_ts = now
            return True
        return False

    def release_lock(self) -> None:
        """Cleanly releases primary node lock upon graceful shutdown."""
        if self.is_active and self._global_lock_state and self._global_lock_state.node_id == self.node_id:
            logger.info(f"[HA_LOCK_RELEASED] Node {self.node_id} cleanly released primary lock.")
            self._global_lock_state = None
            self.is_active = False
