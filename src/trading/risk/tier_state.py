"""Persistent risk-tier state for the SurvivalEngine.

Survives process restarts so a daemon cannot re-enter NORMAL merely by
restarting while the account is still in a defended state.

Two storage backends behind one interface:

- ``RedisTierState`` (selected when ``REDIS_URL`` is set): JSON payload under a
  Redis key (``RISK_TIER_STATE_KEY`` env or ``trading:risk:tier_state``),
  namespaced per scope (R2 / VA-016): ``<base>:<scope>`` with ``scope``
  defaulting to ``'global'`` (legacy single-portfolio behaviour), stored with
  SETEX so stale entries expire.
- File backend (default when ``REDIS_URL`` is unset): atomic tmp+os.replace JSON
  file (legacy behaviour, byte-compatible).

Failure policy (R2 / VA-015 + VA-062) -- capital-defense state fails CLOSED,
not open:

* Every successful load/save refreshes an in-process last-known cache.
* On a Redis connection error during load, the last-known cached state is
  returned instead of resetting to NORMAL. With no cache and no usable
  fallback the backend returns the ``UNKNOWN`` tier, which callers must treat
  as a CAUTION-minimum (never NORMAL).
* Saves hit the in-memory cache FIRST (synchronously), then attempt async-ish
  persistence; a Redis outage mid-save therefore cannot lose a transition.
* At construction the engine merges cached + persisted state keeping the MORE
  defensive tier (``more_defensive``), so neither source can silently launder
  a defended tier.
"""

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("trading.risk.tier_state")

STATE_FILE_ENV = "RISK_TIER_STATE_FILE"
DEFAULT_STATE_FILE = ".risk_tier_state.json"

REDIS_URL_ENV = "REDIS_URL"
STATE_KEY_ENV = "RISK_TIER_STATE_KEY"
DEFAULT_REDIS_KEY = "trading:risk:tier_state"
DEFAULT_REDIS_TTL_SEC = 7 * 24 * 3600

# R2 / VA-015: tier reported when neither Redis nor any cache/fallback can be
# consulted. Consumers MUST treat UNKNOWN as a CAUTION-minimum (throttle),
# never as NORMAL.
UNKNOWN_TIER = "unknown"
DEFAULT_SCOPE = "global"

# Severity ranking used by more_defensive() -- mirrors SurvivalTier ordering
# (kept local to avoid a circular import: survival.py imports this module).
# UNKNOWN ranks at SURVIVAL-level defensiveness for merge purposes.
_TIER_SEVERITY = {
    "normal": 0,
    "caution": 1,
    "survival": 2,
    UNKNOWN_TIER: 2,
    "cooldown": 3,
}

# R2 / VA-015/VA-062: process-wide last-known-good state per resolved key.
_LAST_KNOWN: Dict[str, "TierState"] = {}
_CACHE_LOCK = threading.Lock()


@dataclass
class TierState:
    """Hysteresis bookkeeping for the survival tier automaton.

    ``last_settled_equity`` (R2 / VB-001) carries the previous cycle's settled
    equity so the exact-Decimal daily-loss dual-run keeps a usable denominator
    on sessions where ``day_start_settled_equity`` was never recorded. Optional
    with a default so old persisted payloads remain readable.
    """

    tier: str = "normal"        # SurvivalTier.value
    entered_cycle: int = 0      # cycle index at which current tier was entered
    below_count: int = 0        # consecutive cycles evaluating BELOW current tier
    last_settled_equity: Optional[float] = None


def _state_from_mapping(data: Any) -> TierState:
    """Parse a persisted mapping into TierState (raises ValueError/TypeError on
    malformed content -- callers decide the failure policy)."""
    anchor = data.get("last_settled_equity")
    return TierState(
        tier=str(data.get("tier", "normal")),
        entered_cycle=int(data.get("entered_cycle", 0)),
        below_count=int(data.get("below_count", 0)),
        last_settled_equity=(float(anchor) if anchor is not None else None),
    )


def resolve_state_path(path: Optional[str] = None) -> str:
    if path:
        return path
    return os.environ.get(STATE_FILE_ENV, DEFAULT_STATE_FILE)


def default_state() -> TierState:
    """Fresh NORMAL bookkeeping. NOTE: backend failures no longer return this
    blindly -- see load()/load_state() fail-closed policy."""
    return TierState(tier="normal", entered_cycle=0, below_count=0)


def unknown_state() -> TierState:
    """The CAUTION-minimum stand-in returned when nothing is known."""
    return TierState(tier=UNKNOWN_TIER, entered_cycle=0, below_count=0)


def more_defensive(a: TierState, b: TierState) -> TierState:
    """Return whichever state defends capital more aggressively (R2 / VA-062).

    Used to merge in-memory and persisted state at startup so a stale NORMAL
    on either side can never launder a defended tier recorded elsewhere.
    Unrecognized tier strings rank at UNKNOWN defensiveness. Ties keep ``a``
    (the fresher in-memory value).
    """
    sev_a = _TIER_SEVERITY.get(str(getattr(a, "tier", "")).lower(), _TIER_SEVERITY[UNKNOWN_TIER])
    sev_b = _TIER_SEVERITY.get(str(getattr(b, "tier", "")).lower(), _TIER_SEVERITY[UNKNOWN_TIER])
    return a if sev_a >= sev_b else b


def _cache_put(key: str, state: TierState) -> None:
    with _CACHE_LOCK:
        _LAST_KNOWN[key] = state


def _cache_get(key: str) -> Optional[TierState]:
    with _CACHE_LOCK:
        cached = _LAST_KNOWN.get(key)
        return TierState(**asdict(cached)) if cached is not None else None


class RedisTierState:
    """Redis-backed tier persistence (SETEX of the JSON payload).

    R2 / VA-016: the effective key is ``<base>:<scope>``; ``scope`` defaults to
    'global' so existing deployments keep their legacy single key.
    R2 / VA-015 + VA-062: every successful load/save refreshes the in-process
    last-known cache; saves hit that cache FIRST (synchronously) before
    attempting persistence, and loads fail CLOSED (cached state, else UNKNOWN)
    instead of resetting to NORMAL.
    """

    def __init__(
        self,
        client: Any = None,
        redis_url: Optional[str] = None,
        key: Optional[str] = None,
        ttl_sec: int = DEFAULT_REDIS_TTL_SEC,
        scope: str = DEFAULT_SCOPE,
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
        self.ttl_sec = int(ttl_sec)
        self.scope = scope or DEFAULT_SCOPE
        base = key or os.environ.get(STATE_KEY_ENV, DEFAULT_REDIS_KEY)
        # R2 / VA-016: per-symbol/per-portfolio namespacing. The legacy global
        # scope maps onto the bare base key for byte-compatibility with data
        # written before scoping existed.
        self.base_key = base
        self.key = base if self.scope == DEFAULT_SCOPE else f"{base}:{self.scope}"

    def refresh_ttl(self) -> bool:
        """VA-072: touch the key to prevent silent expiry of active defense.
        Returns True on success, False if the key doesn't exist or Redis fails."""
        try:
            return bool(self.client.expire(self.key, self.ttl_sec))
        except Exception as exc:  # noqa: BLE001 — best-effort; defense unchanged
            logger.warning("Could not refresh TTL for key %s (%s)", self.key, exc)
            return False

    def save(self, state: TierState) -> bool:
        # R2 / VA-062 write-behind step 1: cache synchronously BEFORE any I/O,
        # so an outage mid-persist cannot lose the transition.
        _cache_put(self.key, state)
        try:
            self.client.setex(self.key, self.ttl_sec, json.dumps(asdict(state)))
            self.refresh_ttl()  # VA-072: ensure heartbeat keeps defense alive
            return True
        except Exception as exc:  # noqa: BLE001 — cached above; persist best-effort
            logger.warning(
                "Could not save risk tier state to redis key %s (%s); held in "
                "in-process memory and will re-persist on the next save",
                self.key, exc,
            )
            return False

    def load(self) -> TierState:
        try:
            raw = self.client.get(self.key)
        except Exception as exc:  # noqa: BLE001 — fail CLOSED (R2 / VA-015)
            cached = _cache_get(self.key)
            if cached is not None:
                logger.warning(
                    "Could not load risk tier state from redis key %s (%s); "
                    "holding last-known in-memory tier '%s' (fail-closed)",
                    self.key, exc, cached.tier,
                )
                return cached
            logger.warning(
                "Could not load risk tier state from redis key %s (%s) and no "
                "in-memory last-known state exists; returning '%s' tier -- "
                "callers MUST treat this as CAUTION-minimum",
                self.key, exc, UNKNOWN_TIER,
            )
            return unknown_state()
        if raw is None:
            return default_state()
        try:
            state = _state_from_mapping(json.loads(raw))
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "Corrupt risk tier state in redis key %s (%s); defaulting to NORMAL", self.key, exc
            )
            return default_state()
        # R2 / VA-062: merge with the in-process last-known cache taking the
        # MORE defensive tier, so neither source can launder a defended state.
        cached = _cache_get(self.key)
        if cached is not None:
            state = more_defensive(state, cached)
        _cache_put(self.key, state)
        return state


def _select_backend(client: Any = None, path: Optional[str] = None, scope: str = DEFAULT_SCOPE):
    """File backend by default; Redis only on explicit client or REDIS_URL."""
    if client is not None:
        return RedisTierState(client=client, scope=scope)
    redis_url = (os.environ.get(REDIS_URL_ENV) or "").strip()
    if not redis_url:
        return None
    if path:
        # Explicit file path wins over ambient env — keeps legacy callers/tests
        # that pass state_path= deterministic even on hosts exporting REDIS_URL.
        logger.debug("Explicit state path %s overrides REDIS_URL for tier state", path)
        return None
    return RedisTierState(redis_url=redis_url, scope=scope)


def save_state(
    state: TierState,
    path: Optional[str] = None,
    client: Any = None,
    scope: str = DEFAULT_SCOPE,
) -> bool:
    """Persist state via the selected backend. Returns True on success.

    R2 / VA-062: the state is written to the in-process last-known cache
    FIRST (synchronously) in every path, then persisted; a storage outage
    therefore cannot lose a tier transition.
    """
    backend = _select_backend(client=client, path=path, scope=scope)
    if backend is not None:
        return backend.save(state)
    target = resolve_state_path(path)
    _cache_put(f"file:{target}", state)
    try:
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(state), fh)
        os.replace(tmp, target)
        return True
    except OSError as exc:
        logger.warning("Could not save risk tier state to %s: %s", target, exc)
        return False


def load_state(
    path: Optional[str] = None,
    client: Any = None,
    scope: str = DEFAULT_SCOPE,
) -> TierState:
    """Load persisted state (R2 / VA-015: fails CLOSED).

    On a storage/connection error the last-known cached state is returned;
    with no cache and no file fallback configured the ``UNKNOWN`` tier is
    returned -- callers MUST treat UNKNOWN as a CAUTION-minimum. Only
    "genuinely no record" / corrupt-record cases still yield NORMAL.
    """
    backend = _select_backend(client=client, path=path, scope=scope)
    if backend is not None:
        return backend.load()
    target = resolve_state_path(path)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        state = _state_from_mapping(raw)
    except FileNotFoundError:
        # No record anywhere is NOT a connection error: a fresh deployment
        # legitimately starts NORMAL.
        return default_state()
    except json.JSONDecodeError as exc:
        # Corrupt RECORD (readable store, unparsable content): legacy policy
        # applies -- this is not a transport failure, so NORMAL stands.
        logger.warning(
            "Could not load risk tier state from %s (%s); defaulting to NORMAL", target, exc
        )
        return default_state()
    except (OSError, ValueError, TypeError) as exc:
        cached = _cache_get(f"file:{target}")
        if cached is not None:
            logger.warning(
                "Could not load risk tier state from %s (%s); holding "
                "last-known in-memory tier '%s' (fail-closed)",
                target, exc, cached.tier,
            )
            return cached
        logger.warning(
            "Could not load risk tier state from %s (%s); returning '%s' tier "
            "-- callers MUST treat this as CAUTION-minimum",
            target, exc, UNKNOWN_TIER,
        )
        return unknown_state()
    _cache_put(f"file:{target}", state)
    return state
