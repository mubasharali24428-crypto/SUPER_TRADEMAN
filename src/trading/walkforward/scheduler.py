"""Walk-forward scheduling: when is the next validation cycle due."""
from __future__ import annotations

import time

STALENESS_DAYS_DEFAULT = 7
_DAY_SECONDS = 86_400


def should_run(
    last_run_ts: float | None,
    now_ts: float | None = None,
    staleness_days: float = STALENESS_DAYS_DEFAULT,
) -> bool:
    """True when the last validation run is older than the staleness window (or never ran)."""
    now = now_ts if now_ts is not None else time.time()
    if last_run_ts is None:
        return True
    return (now - last_run_ts) >= staleness_days * _DAY_SECONDS
