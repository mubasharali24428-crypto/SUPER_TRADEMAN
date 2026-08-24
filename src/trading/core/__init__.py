"""Trading core: Decimal-based money primitives.

Single source of truth for exchange-convention rounding, notional checks, and
float->Decimal conversion. See money.py docstrings for contracts.

TODO(P1-A call-site migration): existing call sites still use float math;
migrating them here is a tracked follow-up wave.
"""

from trading.core.money import (
    check_min_notional,
    decimal_from_float,
    quantize_to_step,
)

__all__ = [
    "check_min_notional",
    "decimal_from_float",
    "quantize_to_step",
]
