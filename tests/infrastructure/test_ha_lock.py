"""Tests for Active-Passive HA Distributed Lock and Failover Acquisition.

Real lock semantics only: cross-process exclusion is exercised against Redis
(live server when reachable, else a deterministic in-memory Redis stand-in with
a controllable clock). The legacy tests that monkeypatched one manager's
``_global_lock_state`` onto another were removed — copying private in-process
state proved nothing about real contention and masked the finding that two
processes never contended at all (see sub04_status.md).

Genuine multi-process contention lives in tests/test_two_process_lock.py.
"""

import sys
from pathlib import Path

# The in-memory Redis stand-in ships next to this test file; pytest's default
# import mode puts this directory on sys.path already.
import fake_redis_stub  # noqa: E402
import pytest

from trading.infrastructure.ha_lock import (ActivePassiveManager,
                                            InProcessLockBackend,
                                            RedisLockBackend,
                                            select_lock_backend)

LOCK_NAME = "test:ha:primary"


def _make_pair(client, ttl_sec=5.0, lock_name=LOCK_NAME):
    node_a = ActivePassiveManager(
        node_id="node_a",
        ttl_sec=ttl_sec,
        backend=RedisLockBackend(
            lock_name=lock_name, node_id="node_a", ttl_sec=ttl_sec, client=client
        ),
    )
    node_b = ActivePassiveManager(
        node_id="node_b",
        ttl_sec=ttl_sec,
        backend=RedisLockBackend(
            lock_name=lock_name, node_id="node_b", ttl_sec=ttl_sec, client=client
        ),
    )
    return node_a, node_b


@pytest.fixture()
def memory_client():
    return fake_redis_stub.MemoryRedis()


# ---------------------------------------------------------------------------
# Backend selection / DEGRADED mode
# ---------------------------------------------------------------------------


def test_degraded_warning_when_redis_url_unset(monkeypatch, caplog):
    monkeypatch.delenv("REDIS_URL", raising=False)
    with caplog.at_level("WARNING"):
        backend = select_lock_backend(node_id="n1", ttl_sec=5.0)
    assert isinstance(backend, InProcessLockBackend)
    assert any("DEGRADED" in rec.message for rec in caplog.records), caplog.records


def test_redis_backend_selected_when_redis_url_set(monkeypatch, memory_client):
    # Constructing RedisLockBackend must NOT require a reachable server.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    backend = select_lock_backend(node_id="n1", ttl_sec=5.0, lock_name=LOCK_NAME)
    assert isinstance(backend, RedisLockBackend)


def test_explicit_redis_client_forces_redis_backend(monkeypatch, memory_client):
    monkeypatch.delenv("REDIS_URL", raising=False)
    mgr = ActivePassiveManager(node_id="n1", redis_client=memory_client)
    assert isinstance(mgr.backend, RedisLockBackend)


def test_public_api_preserved(memory_client):
    """acquire_lock / release_lock / is_primary behave identically to legacy API."""
    mgr = ActivePassiveManager(
        node_id="solo",
        backend=RedisLockBackend(
            lock_name=LOCK_NAME, node_id="solo", ttl_sec=5.0, client=memory_client
        ),
    )
    assert mgr.is_primary is False
    assert mgr.acquire_lock() is True
    assert mgr.is_active is True
    assert mgr.is_primary is True
    assert mgr.send_heartbeat() is True
    mgr.release_lock()
    assert mgr.is_active is False
    assert mgr.is_primary is False


# ---------------------------------------------------------------------------
# Real exclusion semantics (in-memory Redis stand-in, deterministic clock)
# ---------------------------------------------------------------------------


def test_second_node_fails_while_first_holds(memory_client):
    node_a, node_b = _make_pair(memory_client)

    assert node_a.acquire_lock() is True
    assert node_b.acquire_lock() is False  # genuine mutual exclusion
    assert node_b.is_active is False
    assert node_b.send_heartbeat() is False  # cannot refresh someone else's lease

    # Heartbeat by the holder keeps it.
    assert node_a.send_heartbeat() is True
    assert node_a.acquire_lock() is True  # re-entrant refresh

    node_a.release_lock()
    assert node_b.acquire_lock() is True  # free after clean release


def test_failover_after_ttl_expiry_no_clean_release(memory_client):
    """Crash simulation: holder never releases; standby takes over post-TTL."""
    # MemoryRedis clocks are milliseconds-since-epoch.
    clock = {"now_ms": 1_000_000.0}
    client = fake_redis_stub.MemoryRedis(clock=lambda: clock["now_ms"])
    node_a, node_b = _make_pair(client, ttl_sec=5.0)

    assert node_a.acquire_lock() is True
    clock["now_ms"] += 3_000.0
    assert node_b.acquire_lock() is False  # still inside TTL window
    clock["now_ms"] += 3_000.0  # 6s > 5s TTL: Redis key expired
    assert node_b.acquire_lock() is True  # deterministic failover
    assert node_b.is_active is True


def test_release_never_drops_another_holders_lock(memory_client):
    """Token-scoped release: an ex-holder cannot delete the new holder's key."""
    clock = {"now_ms": 2_000_000.0}
    client = fake_redis_stub.MemoryRedis(clock=lambda: clock["now_ms"])
    node_a, node_b = _make_pair(client, ttl_sec=5.0)

    assert node_a.acquire_lock() is True
    clock["now_ms"] += 6_000.0  # A's lease expires silently (crash)
    assert node_b.acquire_lock() is True

    # Zombie A wakes up and tries a clean shutdown release.
    node_a.release_lock()

    assert isinstance(node_b.backend, RedisLockBackend)
    stored = client.get(LOCK_NAME)
    assert stored == node_b.backend.token  # B still owns the lock
    assert client.ttl(LOCK_NAME) > 0
    assert node_b.is_active is True


def test_fencing_tokens_monotonic_across_nodes(memory_client):
    node_a, node_b = _make_pair(memory_client)

    t1 = node_a.fencing_token()
    assert t1 is not None
    t2 = node_b.fencing_token()
    assert t2 is not None
    t3 = node_a.fencing_token()
    assert t3 is not None
    assert t2 == t1 + 1
    assert t3 == t2 + 1


def test_legacy_in_process_backend_still_works():
    """Fallback backend keeps legacy single-process behaviour (incl. takeover)."""
    backend = InProcessLockBackend(node_id="legacy", ttl_sec=15.0)
    assert backend.acquire(now=100.0) is True
    assert backend.heartbeat(now=105.0) is True
    assert backend.acquire(now=110.0) is True  # re-entrant
    other = InProcessLockBackend(node_id="intruder", ttl_sec=15.0)
    other.state = backend.state  # simulate shared view within ONE process
    assert other.acquire(now=110.0) is False  # inside TTL
    assert other.acquire(now=200.0) is True  # expired -> takeover
    backend.release()


def test_shutdown_handler_releases_redis_lock(memory_client):
    """GracefulShutdownHandler integration: release path clears the Redis key."""
    from trading.infrastructure.shutdown import GracefulShutdownHandler
    from trading.security.audit_ledger import AuditLedger

    mgr = ActivePassiveManager(
        node_id="node_x",
        backend=RedisLockBackend(
            lock_name=LOCK_NAME, node_id="node_x", ttl_sec=5.0, client=memory_client
        ),
    )
    assert mgr.acquire_lock() is True
    handler = GracefulShutdownHandler(ha_manager=mgr, audit_ledger=AuditLedger())
    handler.handle_signal(15)
    assert not mgr.is_active
    assert memory_client.get(LOCK_NAME) is None  # key really deleted in the store
