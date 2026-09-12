"""Statistical validation and cross-validation tools for trading backtests."""

from trading.stats.cross_validation import (CPCVConfig, TrainTestSplit,
                                            apply_split, generate_cpcv_splits)
from trading.stats.effective_trials import effective_trials
from trading.stats.pbo import CSCVConfig, PBOResult, compute_pbo_cscv
from trading.stats.sharpe_variants import (deflated_sharpe_ratio,
                                           expected_max_sharpe,
                                           probabilistic_sharpe_ratio)

__all__ = [
    "CPCVConfig",
    "TrainTestSplit",
    "generate_cpcv_splits",
    "apply_split",
    "compute_pbo_cscv",
    "CSCVConfig",
    "PBOResult",
    "effective_trials",
    "deflated_sharpe_ratio",
    "probabilistic_sharpe_ratio",
    "expected_max_sharpe",
]
