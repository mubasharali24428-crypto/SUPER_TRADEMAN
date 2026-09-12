"""Shared in-memory Redis-compatible store server for two-process lock tests.

Serves ONE ``MemoryRedis`` singleton over ``multiprocessing.managers`` so real
OS processes can contend over genuinely shared lock state without Redis.
Every connected client's ``get_store()`` returns a proxy to the SAME
singleton object living in the server process.

Usage (test parent):
    server = start_store_server(("127.0.0.1", free_port), AUTHKEY)
    ...
    server.shutdown()

Usage (any process):
    store = connect_store(("127.0.0.1", port), AUTHKEY)
    store.set("k", "v", nx=True, px=1000)
"""

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from multiprocessing.managers import BaseManager  # noqa: E402

import fake_redis_stub  # noqa: E402 — resolved via the sys.path line above

AUTHKEY = b"alexforce-sub04-two-process-lock"

_singleton = None


def get_store():
    """Return THE shared MemoryRedis instance (server-side callable)."""
    global _singleton
    if _singleton is None:
        _singleton = fake_redis_stub.MemoryRedis()
    return _singleton


class _StoreServer(BaseManager):
    pass


class _StoreClient(BaseManager):
    pass


_ServerBooted = False


def _ensure_registration():
    global _ServerBooted
    _StoreServer.register("get_store", callable=get_store)
    _StoreClient.register("get_store")  # proxy-only; callable resolved server-side
    _ServerBooted = True


def start_store_server(address, authkey=AUTHKEY):
    """Boot the shared-store server process; returns the manager (use .shutdown())."""
    _ensure_registration()
    server = _StoreServer(address=tuple(address), authkey=authkey)
    server.start()
    return server


def connect_store(address, authkey=AUTHKEY):
    """Attach to the shared store from any process; returns a proxy."""
    _ensure_registration()
    client = _StoreClient(address=tuple(address), authkey=authkey)
    client.connect()
    return getattr(
        client, "get_store"
    )()  # dynamic proxy attr (BaseManager registration)
