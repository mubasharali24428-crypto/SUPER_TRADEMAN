"""Active-Passive Distributed Lock & High Availability Heartbeat Manager.

Two lock backends behind one interface:

- ``RedisLockBackend`` (selected when ``REDIS_URL`` is set): true cross-process
  mutual exclusion. Acquisition is ``SET <name> <token> NX PX <ttl_ms>`` with a
  random uuid4-hex token; release runs a Lua compare-and-delete so a node can
  never drop a lock now owned by another node. Fencing tokens are monotonically
  increasing values drawn from ``INCR <name>:fence`` (safe ordering signal for
  downstream resource guards). Failover falls out of Redis key expiry: when the
  active node dies, its key evaporates at TTL and any standby acquires cleanly.
- ``InProcessLockBackend`` (fallback, only when ``REDIS_URL`` is unset): the
  legacy instance-local behaviour. This is DEGRADED mode — two processes never
  contend because the lock state lives in each process's memory. A warning is
  logged at construction so deployments cannot miss it.

Public API preserved: ``acquire_lock`` / ``release_lock`` / ``is_primary``
(plus the pre-existing ``is_active`` flag and ``send_heartbeat``).
"""

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from trading.observability.logger import get_logger

__all__ = [
    "ActivePassiveManager",
    "InProcessLockBackend",
    "LockBackend",
    "LockState",
    "RedisLockBackend",
    "select_lock_backend",
]

logger = get_logger("trading.infrastructure.ha_lock")

REDIS_URL_ENV = "REDIS_URL"
DEFAULT_LOCK_NAME = "trading:ha:primary"

# Compare-and-delete release: only the process whose token matches the stored
# value may delete the key (standard Redlock-safe release, avoids releasing a
# lock that expired and was re-acquired by another node while we were slow).
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


@dataclass
class LockState:
    """Legacy instance-local lock bookkeeping (in-process backend only)."""

    node_id: str
    last_heartbeat_ts: float
    ttl_sec: float = 15.0


class LockBackend:
    """Interface shared by all HA lock backends."""

    #: Human-readable backend tag for logs/tests.
    name: str = "base"

    def acquire(self, now: float) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def heartbeat(self, now: float) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def release(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def next_fencing_token(self) -> Optional[int]:
        """Return a monotonically increasing fencing token, if supported."""
        return None

    @property
    def held_by_us(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class InProcessLockBackend(LockBackend):
    """Legacy single-process lock state (DEGRADED mode; no cross-process safety)."""

    name = "in-process"

    def __init__(self, node_id: str, ttl_sec: float):
        self.node_id = node_id
        self.ttl_sec = ttl_sec
        self.state: Optional[LockState] = None
        self._fence_counter = 0

    @property
    def held_by_us(self) -> bool:
        return self.state is not None and self.state.node_id == self.node_id

    def acquire(self, now: float) -> bool:
        if self.state is None:
            self.state = LockState(node_id=self.node_id, last_heartbeat_ts=now, ttl_sec=self.ttl_sec)
            return True
        if self.state.node_id == self.node_id:
            self.state.last_heartbeat_ts = now
            return True
        # Check if current holder lock has expired
        elapsed = now - self.state.last_heartbeat_ts
        if elapsed > self.state.ttl_sec:
            logger.warning(
                f"[HA_FAILOVER_TRIGGERED] Primary node {self.state.node_id} lock expired "
                f"(age {elapsed:.1f}s > {self.ttl_sec}s). Node {self.node_id} taking over primary lock."
            )
            self.state = LockState(node_id=self.node_id, last_heartbeat_ts=now, ttl_sec=self.ttl_sec)
            return True
        return False

    def heartbeat(self, now: float) -> bool:
        state = self.state
        if state is not None and state.node_id == self.node_id:
            state.last_heartbeat_ts = now
            return True
        return False

    def release(self) -> None:
        if self.held_by_us:
            logger.info(f"[HA_LOCK_RELEASED] Node {self.node_id} cleanly released primary lock.")
            self.state = None

    def next_fencing_token(self) -> int:
        """Best-effort monotonic counter (process-local only in DEGRADED mode)."""
        self._fence_counter += 1
        return self._fence_counter


def import_redis_client():
    try:
        import redis  # noqa: PLC0415 — deliberate lazy import (optional dependency)
    except ImportError as exc:  # pragma: no cover - depends on venv contents
        raise RuntimeError(
            "REDIS_URL is set but the 'redis' package is not available in this venv. "
            "Install redis-py to use the distributed lock/state backend."
        ) from exc
    return redis


class RedisLockBackend(LockBackend):
    """Redis-backed distributed lock (SET NX PX + Lua compare-and-del release)."""

    name = "redis"

    def __init__(
        self,
        lock_name: str,
        node_id: str,
        ttl_sec: float,
        client: Any = None,
        redis_url: Optional[str] = None,
    ):
        if client is None:
            redis_mod = import_redis_client()
            url = redis_url or os.environ.get(REDIS_URL_ENV)
            if not url:
                raise ValueError("RedisLockBackend requires a client or REDIS_URL")
            client = redis_mod.Redis.from_url(url, decode_responses=True)
        self.client = client
        self.lock_name = lock_name
        self.node_id = node_id
        self.ttl_sec = ttl_sec
        self.token: Optional[str] = None
        self._fence_key = f"{lock_name}:fence"

    @property
    def _ttl_ms(self) -> int:
        return max(1, int(round(self.ttl_sec * 1000)))

    @property
    def held_by_us(self) -> bool:
        return self.is_active and self.token is not None

    # ``is_active`` mirrors whether WE currently hold the lock.
    @property
    def is_active(self) -> bool:
        return getattr(self, "_is_active", False)

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self._is_active = value

    def acquire(self, now: float) -> bool:
        """now is accepted for API symmetry; expiry is enforced by Redis PX TTL."""
        del now
        token = uuid.uuid4().hex
        try:
            acquired = bool(self.client.set(self.lock_name, token, nx=True, px=self._ttl_ms))
        except Exception as exc:  # noqa: BLE001 — VA-017: transport failure treated as not-held
            logger.warning("RedisLockBackend.acquire set() failed: %s", exc)
            return False
        if acquired:
            self.token = token
            self.is_active = True
            return True
        # Re-entrant refresh: if the stored value is OUR token we still hold it.
        try:
            current = self.client.get(self.lock_name)
        except Exception:  # noqa: BLE001 — transport hiccup treated as not-held
            current = None
        if current == self.token and self.token is not None:
            self.client.pexpire(self.lock_name, self._ttl_ms)
            self.is_active = True
            return True
        self.is_active = False
        return False

    def heartbeat(self, now: float) -> bool:
        del now
        if not self.held_by_us:
            return False
        try:
            current = self.client.get(self.lock_name)
        except Exception:  # noqa: BLE001
            return False
        if current != self.token:
            self.is_active = False
            self.token = None
            return False
        self.client.pexpire(self.lock_name, self._ttl_ms)
        return True

    def release(self) -> None:
        """Compare-and-delete: removes the key ONLY if we still own it."""
        if not self.held_by_us:
            return
        try:
            released = int(self.client.eval(_RELEASE_LUA, 1, self.lock_name, self.token))
        except Exception:  # noqa: BLE001 — Lua-less clients (e.g. some fakes): WATCH-CAS fallback
            released = self._release_watch_cas()
        if released:
            logger.info(f"[HA_LOCK_RELEASED] Node {self.node_id} cleanly released primary lock.")
        self.token = None
        self.is_active = False

    def _release_watch_cas(self) -> int:
        """Transaction-based compare-and-delete (fallback when EVAL unavailable)."""
        token = self.token
        if token is None:
            return 0
        with self.client.pipeline() as pipe:
            try:
                pipe.watch(self.lock_name)
                if pipe.get(self.lock_name) == token:
                    pipe.multi()
                    pipe.delete(self.lock_name)
                    pipe.execute()
                    return 1
                pipe.unwatch()
                return 0
            except Exception as exc:  # noqa: BLE001 — lock liveness must not crash shutdown
                logger.warning(f"[HA_LOCK_RELEASE_ERROR] node={self.node_id} error={exc}")
                return 0

    def next_fencing_token(self) -> int:
        """INCR <lock_name>:fence — strictly monotonic across all processes."""
        return int(self.client.incr(self._fence_key))


def select_lock_backend(node_id: str, ttl_sec: float, lock_name: Optional[str] = None) -> LockBackend:
    """Choose Redis backend when REDIS_URL is set; DEGRADED in-process otherwise."""
    redis_url = os.environ.get(REDIS_URL_ENV, "").strip()
    name = lock_name or DEFAULT_LOCK_NAME
    if redis_url:
        logger.info(f"[HA_LOCK_BACKEND] redis url-configured node={node_id} lock={name}")
        return RedisLockBackend(lock_name=name, node_id=node_id, ttl_sec=ttl_sec, redis_url=redis_url)
    logger.warning(
        "[HA_DEGRADED_MODE] REDIS_URL is unset: falling back to INSTANCE-LOCAL active/passive "
        "lock state. Multiple processes will NOT contend for the primary lock and each will "
        "believe it is primary. Set REDIS_URL to enable cross-process exclusion."
    )
    return InProcessLockBackend(node_id=node_id, ttl_sec=ttl_sec)


class ActivePassiveManager:
    """Manages active-passive failover distributed lock with TTL expiration.

    Backend selection happens once at construction: Redis when ``REDIS_URL`` is
    set (or an explicit ``redis_client`` is injected), legacy in-process state
    otherwise (with a DEGRADED-mode warning).
    """

    def __init__(
        self,
        node_id: str,
        ttl_sec: float = 15.0,
        lock_name: Optional[str] = None,
        redis_client: Any = None,
        backend: Optional[LockBackend] = None,
    ):
        self.node_id = node_id
        self.ttl_sec = ttl_sec
        if backend is not None:
            self.backend = backend
        elif redis_client is not None:
            self.backend = RedisLockBackend(
                lock_name=lock_name or DEFAULT_LOCK_NAME, node_id=node_id, ttl_sec=ttl_sec, client=redis_client
            )
        else:
            self.backend = select_lock_backend(node_id=node_id, ttl_sec=ttl_sec, lock_name=lock_name)
        self.is_active = False

    @property
    def is_primary(self) -> bool:
        """Public alias for the active flag."""
        return self.is_active

    def acquire_lock(self, current_time: Optional[float] = None) -> bool:
        """Attempts to acquire primary active node lock."""
        now = current_time if current_time is not None else time.time()
        was_active = self.is_active
        self.is_active = self.backend.acquire(now)
        if self.is_active and not was_active:
            logger.info(f"[HA_LOCK_ACQUIRED] Node {self.node_id} acquired primary active lock.")
        return self.is_active

    def send_heartbeat(self, current_time: Optional[float] = None) -> bool:
        """Refreshes primary node heartbeat/TTL."""
        now = current_time if current_time is not None else time.time()
        refreshed = self.backend.heartbeat(now)
        if not refreshed:
            self.is_active = False
        return refreshed

    def release_lock(self) -> None:
        """Cleanly releases primary node lock upon graceful shutdown."""
        # Backends emit their own release log lines.
        self.backend.release()
        self.is_active = False

    def fencing_token(self) -> Optional[int]:
        """Monotonically increasing fencing token for downstream guards."""
        return self.backend.next_fencing_token()
