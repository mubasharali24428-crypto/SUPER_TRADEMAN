"""True two-process contention tests for the HA distributed lock.

The legacy infrastructure tests monkeypatched ``_global_lock_state`` between
managers — which proved nothing, because that instance-local state is exactly
why two processes never contended (sub-04 finding). These tests spawn REAL OS
processes via subprocess and assert genuine cross-process exclusion:

- a contender process must FAIL to acquire while the parent holds the lock;
- the same contender must SUCCEED after the parent releases.

Variants:
  - ``test_*_real_redis_*``: runs against REDIS_URL (or localhost:6379 when a
    server is actually reachable); skipped otherwise.
  - ``test_two_process_exclusion_deterministic_memory_channel``: ALWAYS runs.
    Two real processes contend over a shared in-memory store served by
    ``multiprocessing.managers.BaseManager`` — no Redis required, no sleeps on
    the assertion path.
"""

import socket
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
CONTENDER = TESTS_DIR / "two_process_contender.py"

sys.path.insert(0, str(TESTS_DIR / "infrastructure"))
import fake_redis_stub  # noqa: E402


def _redis_reachable(url: str) -> bool:
    try:
        import redis as redis_mod

        client = redis_mod.Redis.from_url(url, socket_connect_timeout=0.5)
        return bool(client.ping())
    except Exception:  # noqa: BLE001 — any failure means "not usable"
        return False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _verdict(proc: subprocess.CompletedProcess) -> str:
    """First ACQUIRED/DENIED line of contender stdout (skip JSON log lines)."""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line in ("ACQUIRED", "DENIED"):
            return line
    return ""


def _run_contender(
    store_url: str, lock_name: str, node_id: str, hold_sec: float = 0.0
) -> str:
    """Spawn one contender process; return its first verdict line."""
    proc = subprocess.run(
        [sys.executable, str(CONTENDER), store_url, lock_name, node_id, str(hold_sec)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert (
        proc.returncode == 0
    ), f"contender crashed rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    verdict = _verdict(proc)
    assert verdict, f"contender produced no verdict line: {proc.stdout!r}"
    return verdict


# ---------------------------------------------------------------------------
# Real Redis variant (skipif when no server / no REDIS_URL)
# ---------------------------------------------------------------------------

_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/15"
REDIS_URL_ENV = None
try:  # honour an ambient REDIS_URL if the host exports one
    import os as _os

    REDIS_URL_ENV = _os.environ.get("REDIS_URL")
except Exception:  # pragma: no cover
    pass

REAL_REDIS_URL = REDIS_URL_ENV or _DEFAULT_REDIS_URL

requires_real_redis = pytest.mark.skipif(
    not _redis_reachable(REAL_REDIS_URL),
    reason=f"no reachable Redis at {REAL_REDIS_URL} and REDIS_URL unset",
)


@requires_real_redis
def test_two_process_lock_real_redis_denied_then_acquired():
    """Subprocess contender FAILS while parent holds; succeeds after release."""
    import time as _time

    from trading.infrastructure.ha_lock import (ActivePassiveManager,
                                                RedisLockBackend)

    lock_name = "test:two_process_lock:real"
    backend = RedisLockBackend(
        lock_name=lock_name, node_id="parent", ttl_sec=5.0, redis_url=REAL_REDIS_URL
    )
    parent = ActivePassiveManager(node_id="parent", ttl_sec=5.0, backend=backend)

    try:
        assert parent.acquire_lock() is True
        verdict = _run_contender(REAL_REDIS_URL, lock_name, "child_a")
        assert (
            verdict == "DENIED"
        ), "second process MUST fail while first holds the lock"

        parent.release_lock()
        verdict_after = _run_contender(REAL_REDIS_URL, lock_name, "child_b")
        assert (
            verdict_after == "ACQUIRED"
        ), "same contender class must win after release"
    finally:
        parent.release_lock()


@requires_real_redis
def test_two_process_holder_holds_until_ttl_or_release():
    """Holder keeps the lock across heartbeats; contender stays denied."""
    from trading.infrastructure.ha_lock import (ActivePassiveManager,
                                                RedisLockBackend)

    lock_name = "test:two_process_hold:real"
    backend = RedisLockBackend(
        lock_name=lock_name, node_id="holder", ttl_sec=5.0, redis_url=REAL_REDIS_URL
    )
    holder = ActivePassiveManager(node_id="holder", ttl_sec=5.0, backend=backend)

    try:
        assert holder.acquire_lock() is True
        # Contender denied while we hold (fresh process each time).
        for child in ("c1", "c2"):
            assert _run_contender(REAL_REDIS_URL, lock_name, child) == "DENIED"
        # Heartbeat refreshes our lease; still ours.
        assert holder.send_heartbeat() is True
        assert _run_contender(REAL_REDIS_URL, lock_name, "c3") == "DENIED"
    finally:
        holder.release_lock()
    assert _run_contender(REAL_REDIS_URL, lock_name, "c4") == "ACQUIRED"


# ---------------------------------------------------------------------------
# Deterministic variant — always runs, no Redis needed
# ---------------------------------------------------------------------------


def test_two_process_exclusion_deterministic_memory_channel():
    """Two REAL processes contend over a shared in-memory store (BaseManager).

    Asserts exactly the task's core semantics without any external service:
    second process FAILS while the first holds; SUCCEEDS after release. The
    shared store lives in a third process (BaseManager server singleton), so
    this is genuine cross-process contention, not in-process bookkeeping.
    """
    import memory_store_server

    port = _free_port()
    server = memory_store_server.start_store_server(("127.0.0.1", port))

    try:
        store_url = f"memory://127.0.0.1:{port}"
        proxy = memory_store_server.connect_store(("127.0.0.1", port))

        from trading.infrastructure.ha_lock import (ActivePassiveManager,
                                                    RedisLockBackend)

        lock_name = "test:two_process_lock:mem"
        parent = ActivePassiveManager(
            node_id="parent",
            ttl_sec=10.0,
            backend=RedisLockBackend(
                lock_name=lock_name, node_id="parent", ttl_sec=10.0, client=proxy
            ),
        )

        # Parent acquires IN THIS process; contenders run in OTHER processes.
        assert parent.acquire_lock() is True
        assert isinstance(parent.backend, RedisLockBackend)
        assert proxy.get(lock_name) == parent.backend.token  # state truly shared
        try:
            assert (
                _run_contender(store_url, lock_name, "child_a") == "DENIED"
            ), "second process must FAIL while first holds the lock"
            assert _run_contender(store_url, lock_name, "child_b") == "DENIED"
        finally:
            parent.release_lock()

        # After release, a fresh contender process must WIN.
        assert (
            _run_contender(store_url, lock_name, "child_c") == "ACQUIRED"
        ), "contender must succeed after release"

        # Re-exclusion: whoever holds again denies everyone else cross-process.
        assert parent.acquire_lock() is True
        try:
            assert _run_contender(store_url, lock_name, "child_d") == "DENIED"
        finally:
            parent.release_lock()
    finally:
        server.shutdown()
