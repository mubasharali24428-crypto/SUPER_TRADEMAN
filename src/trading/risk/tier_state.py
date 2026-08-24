"""Persistent risk-tier state for the SurvivalEngine.

Survives process restarts so a daemon cannot re-enter NORMAL merely by
restarting while the account is still in a defended state.

Two storage backends behind one interface:

- ``RedisTierState`` (selected when ``REDIS_URL`` is set): JSON payload under a
  single Redis key (``RISK_TIER_STATE_KEY`` env or ``trading:risk:tier_state``),
  stored with SETEX so stale entries expire. All daemons pointing at the same
  REDIS_URL share one tier history — a restarted passive can't see a fresher
  NORMAL than the active recorded.
- File backend (default when ``REDIS_URL`` is unset): atomic tmp+os.replace JSON
  file (legacy behaviour, byte-compatible).

A failed/corrupt load fails OPEN to a default NORMAL state (state-store failure
must never block trading telemetry) with a warning.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional

logger = logging.getLogger("trading.risk.tier_state")

STATE_FILE_ENV = "RISK_TIER_STATE_FILE"
DEFAULT_STATE_FILE = ".risk_tier_state.json"

REDIS_URL_ENV = "REDIS_URL"
STATE_KEY_ENV = "RISK_TIER_STATE_KEY"
DEFAULT_REDIS_KEY = "trading:risk:tier_state"
DEFAULT_REDIS_TTL_SEC = 7 * 24 * 3600


@dataclass
class TierState:
    """Hysteresis bookkeeping for the survival tier automaton."""

    tier: str = "normal"        # SurvivalTier.value
    entered_cycle: int = 0      # cycle index at which current tier was entered
    below_count: int = 0        # consecutive cycles evaluating BELOW current tier


def resolve_state_path(path: Optional[str] = None) -> str:
    if path:
        return path
    return os.environ.get(STATE_FILE_ENV, DEFAULT_STATE_FILE)


def default_state() -> TierState:
    return TierState(tier="normal", entered_cycle=0, below_count=0)


class RedisTierState:
    """Redis-backed tier persistence (SETEX of the JSON payload)."""

    def __init__(
        self,
        client: Any = None,
        redis_url: Optional[str] = None,
        key: Optional[str] = None,
        ttl_sec: int = DEFAULT_REDIS_TTL_SEC,
    ):
        if client is None:
            try:
                import redis  # noqa: PLC0415 — lazy import (optional dependency)
            except ImportError as exc:  # pragma: no cover - venv-dependent
                raise RuntimeError(
                    "REDIS_URL is set but the 'redis' package is not available in this venv. "
                    "Install redis-py to use the distributed risk-tier state backend."
                ) from exc
            url = redis_url or os.environ.get(REDIS_URL_ENV)
            if not url:
                raise ValueError("RedisTierState requires a client or REDIS_URL")
            client = redis.Redis.from_url(url, decode_responses=True)
        self.client = client
        self.key = key or os.environ.get(STATE_KEY_ENV, DEFAULT_REDIS_KEY)
        self.ttl_sec = int(ttl_sec)

    def save(self, state: TierState) -> bool:
        try:
            self.client.setex(self.key, self.ttl_sec, json.dumps(asdict(state)))
            return True
        except Exception as exc:  # noqa: BLE001 — persistence failure must not block trading
            logger.warning("Could not save risk tier state to redis key %s: %s", self.key, exc)
            return False

    def load(self) -> TierState:
        try:
            raw = self.client.get(self.key)
        except Exception as exc:  # noqa: BLE001 — fail-open like the file backend
            logger.warning(
                "Could not load risk tier state from redis key %s (%s); defaulting to NORMAL",
                self.key, exc,
            )
            return default_state()
        if raw is None:
            return default_state()
        try:
            data = json.loads(raw)
            return TierState(
                tier=str(data.get("tier", "normal")),
                entered_cycle=int(data.get("entered_cycle", 0)),
                below_count=int(data.get("below_count", 0)),
            )
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "Corrupt risk tier state in redis key %s (%s); defaulting to NORMAL", self.key, exc
            )
            return default_state()


def _select_backend(client: Any = None, path: Optional[str] = None):
    """File backend by default; Redis only on explicit client or REDIS_URL."""
    if client is not None:
        return RedisTierState(client=client)
    redis_url = (os.environ.get(REDIS_URL_ENV) or "").strip()
    if not redis_url:
        return None
    if path:
        # Explicit file path wins over ambient env — keeps legacy callers/tests
        # that pass state_path= deterministic even on hosts exporting REDIS_URL.
        logger.debug("Explicit state path %s overrides REDIS_URL for tier state", path)
        return None
    return RedisTierState(redis_url=redis_url)


def save_state(state: TierState, path: Optional[str] = None, client: Any = None) -> bool:
    """Persist state via the selected backend. Returns True on success."""
    backend = _select_backend(client=client, path=path)
    if backend is not None:
        return backend.save(state)
    target = resolve_state_path(path)
    try:
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(state), fh)
        os.replace(tmp, target)
        return True
    except OSError as exc:
        logger.warning("Could not save risk tier state to %s: %s", target, exc)
        return False


def load_state(path: Optional[str] = None, client: Any = None) -> TierState:
    """Load persisted state; ANY failure returns default NORMAL (fail-open)."""
    backend = _select_backend(client=client, path=path)
    if backend is not None:
        return backend.load()
    target = resolve_state_path(path)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return TierState(
            tier=str(raw.get("tier", "normal")),
            entered_cycle=int(raw.get("entered_cycle", 0)),
            below_count=int(raw.get("below_count", 0)),
        )
    except FileNotFoundError:
        return default_state()
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not load risk tier state from %s (%s); defaulting to NORMAL", target, exc)
        return default_state()
