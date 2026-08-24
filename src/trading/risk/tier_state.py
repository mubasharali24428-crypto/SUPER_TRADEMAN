"""Persistent risk-tier state for the SurvivalEngine.

Survives process restarts so a daemon cannot re-enter NORMAL merely by
restarting while the account is still in a defended state. Writes are atomic
(tmp file + os.replace); a failed/corrupt load fails OPEN to a default NORMAL
state (state FILE failure must never block trading telemetry) with a warning.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("trading.risk.tier_state")

STATE_FILE_ENV = "RISK_TIER_STATE_FILE"
DEFAULT_STATE_FILE = ".risk_tier_state.json"


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


def save_state(state: TierState, path: Optional[str] = None) -> bool:
    """Atomically persist state. Returns True on success, False on failure."""
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


def load_state(path: Optional[str] = None) -> TierState:
    """Load persisted state; ANY failure returns default NORMAL (fail-open)."""
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
