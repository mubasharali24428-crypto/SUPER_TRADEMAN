"""Walk-forward package: rolling validation folds, tearsheet reports, scheduling.

Production consumer of the stats stack (compute_pbo_cscv, deflated_sharpe_ratio) —
closes register finding VB-061.
"""
from trading.walkforward.validator import (FoldResult, WalkForwardConfig,
                                           generate_folds, run_walk_forward)

__all__ = [
    "FoldResult",
    "WalkForwardConfig",
    "generate_folds",
    "run_walk_forward",
]
