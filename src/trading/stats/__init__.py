"""Statistical validation and cross-validation tools for trading backtests."""

from trading.stats.cross_validation import (
    CPCVConfig,
    TrainTestSplit,
    apply_split,
    generate_cpcv_splits,
)
from trading.stats.effective_trials import effective_trials
from trading.stats.pbo import compute_pbo

__all__ = [
    "CPCVConfig",
    "TrainTestSplit",
    "generate_cpcv_splits",
    "apply_split",
    "compute_pbo",
    "effective_trials",
]
