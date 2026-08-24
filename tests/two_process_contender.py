#!/usr/bin/env python
"""Contender process used by tests/test_two_process_lock.py.

Usage:
    python two_process_contender.py <store_url> <lock_name> <node_id> [hold_sec] [ttl_sec]

``store_url`` selects the shared lock-state store:
  - ``redis://...``            -> real Redis (cross-machine / cross-process).
  - ``memory://HOST:PORT``     -> attach to a shared in-memory Redis-compatible
                                  store served over multiprocessing.managers
                                  (deterministic, no external services).

Prints exactly one verdict line first — ``ACQUIRED`` or ``DENIED`` — then, if
it won the lock, holds it for ``hold_sec`` seconds (heart-beating once),
releases, prints ``RELEASED`` and exits 0. Any failure exits non-zero. The
parent test reads the first stdout line to learn whether this process got the
lock.
"""

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "tests" / "infrastructure"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from trading.infrastructure.ha_lock import ActivePassiveManager, RedisLockBackend  # noqa: E402


def build_client(store_url: str):
    """Return a redis-API-compatible client for the given store URL."""
    if store_url.startswith("memory://"):
        import memory_store_server

        host, _, port = store_url[len("memory://"):].partition(":")
        return memory_store_server.connect_store((host, int(port)))
    import redis as redis_mod

    return redis_mod.Redis.from_url(store_url, decode_responses=True)


def main(argv):
    store_url, lock_name, node_id = argv[0], argv[1], argv[2]
    hold_sec = float(argv[3]) if len(argv) > 3 else 0.0
    ttl_sec = float(argv[4]) if len(argv) > 4 else 5.0
    if store_url.startswith("redis://"):
        os.environ.setdefault("REDIS_URL", store_url)

    backend = RedisLockBackend(
        lock_name=lock_name, node_id=node_id, ttl_sec=float(ttl_sec), client=build_client(store_url)
    )
    mgr = ActivePassiveManager(node_id=node_id, ttl_sec=float(ttl_sec), backend=backend)
    if mgr.acquire_lock():
        print("ACQUIRED", flush=True)
        if hold_sec > 0:
            time.sleep(hold_sec / 2.0)
            mgr.send_heartbeat()
            time.sleep(hold_sec / 2.0)
        mgr.release_lock()
        print("RELEASED", flush=True)
    else:
        print("DENIED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
