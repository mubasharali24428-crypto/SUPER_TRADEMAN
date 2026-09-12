"""Minimal in-memory Redis stand-in for deterministic HA-lock/state tests.

Implements exactly the operations the distributed-lock / state backends use:
SET(nx=, px=), GET, DELETE, PEXPIRE, INCR, EVAL (compare-and-del pattern),
SCAN_ITER(match=), HGETALL, HINCRBY and the pipeline watch/multi/execute/
unwatch subset used by the WATCH-CAS release fallback. A controllable clock
makes TTL expiry deterministic (no sleeps).
"""

import fnmatch
import time
from typing import Any, Callable, Dict, Optional, Tuple


def _now_ms() -> float:
    return time.time() * 1000.0


class MemoryRedis:
    """Single-process fake with redis-py-compatible method signatures."""

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self.clock = clock or _now_ms
        self._strings: Dict[
            str, Tuple[str, Optional[float]]
        ] = {}  # key -> (value, expires_at_ms)
        self._hashes: Dict[str, Dict[str, str]] = {}

    # -- internals ---------------------------------------------------------
    def _alive(self, key: str) -> bool:
        entry = self._strings.get(key)
        if entry is None:
            return False
        _value, expires_at = entry
        if expires_at is not None and self.clock() >= expires_at:
            del self._strings[key]
            return False
        return True

    # -- strings -----------------------------------------------------------
    def set(
        self, key: str, value: Any, nx: bool = False, px: Optional[int] = None
    ) -> bool:
        if nx and self._alive(key):
            return False
        expires_at = self.clock() + int(px) if px else None
        self._strings[key] = (str(value), expires_at)
        return True

    def get(self, key: str) -> Optional[str]:
        if not self._alive(key):
            return None
        return self._strings[key][0]

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._strings:
                del self._strings[key]
                removed += 1
        return removed

    def pexpire(self, key: str, milliseconds: int) -> bool:
        if not self._alive(key):
            return False
        value, _old = self._strings[key]
        self._strings[key] = (value, self.clock() + int(milliseconds))
        return True

    def setex(self, key: str, seconds: int, value: Any) -> bool:
        """SET with EXpiry (redis-py signature: SETEX key seconds value)."""
        expires_at = self.clock() + max(1, int(seconds)) * 1000
        self._strings[key] = (str(value), expires_at)
        return True

    def ttl(self, key: str) -> int:
        if not self._alive(key):
            return -2
        expires_at = self._strings[key][1]
        return (
            -1
            if expires_at is None
            else max(0, int(round((expires_at - self.clock()) / 1000.0)))
        )

    def incr(self, key: str) -> int:
        current = int(self.get(key) or 0)
        current += 1
        entry = self._strings.get(key)
        expires_at = entry[1] if entry and self._alive(key) else None
        self._strings[key] = (str(current), expires_at)
        return current

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        """Simulate the compare-and-delete release script (the only Lua used)."""
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        if "DEL" in script.upper() and len(keys) == 1 and len(args) == 1:
            if self.get(keys[0]) == args[0]:
                self.delete(keys[0])
                return 1
            return 0
        raise ValueError(
            f"MemoryRedis.eval: unsupported script pattern: {script[:60]!r}"
        )

    # -- scans / hashes ----------------------------------------------------
    def scan_iter(self, match: Optional[str] = None):
        for key in list(self._strings):
            if self._alive(key) and (match is None or fnmatch.fnmatchcase(key, match)):
                yield key

    def hgetall(self, key: str) -> Dict[str, str]:
        return dict(self._hashes.get(key, {}))

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        mapping = self._hashes.setdefault(key, {})
        current = int(mapping.get(field, 0)) + int(amount)
        mapping[field] = str(current)
        return current

    # -- pipelines (WATCH-CAS subset) ---------------------------------------
    def pipeline(self) -> "MemoryPipeline":
        return MemoryPipeline(self)

    # -- misc ---------------------------------------------------------------
    def ping(self) -> bool:
        return True

    def flushdb(self) -> bool:
        self._strings.clear()
        self._hashes.clear()
        return True


class MemoryPipeline:
    """WATCH/MULTI/EXEC emulation sufficient for the CAS release fallback."""

    def __init__(self, client: MemoryRedis):
        self.client = client
        self._commands: Any = []
        self._watched: Optional[str] = None
        self._explicit_multi = False

    def __enter__(self) -> "MemoryPipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def watch(self, key: str) -> None:
        self._watched = key

    def get(self, key: str) -> Optional[str]:
        return self.client.get(key)

    def multi(self) -> None:
        self._explicit_multi = True

    def delete(self, key: str) -> None:
        self._commands.append(("delete", key))

    def execute(self) -> list:
        results = []
        for op, key in self._commands:
            results.append(self.client.delete(key))
        self._commands = []
        return results

    def unwatch(self) -> None:
        self._watched = None
