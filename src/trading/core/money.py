"""Decimal-based money primitives (exchange conventions).

Single source of truth for:
* step-size rounding — exchanges only accept quantities/price on a grid, and
  the convention is to round DOWN so you never request more than intended;
* min-notional gating;
* float -> Decimal conversion that avoids binary-float artifacts.

Pure functions only. TODO(P1-A call-site migration): existing call sites
still do float math; rewiring them onto this module is a tracked follow-up.
"""

from __future__ import annotations

import decimal
from decimal import Decimal, InvalidOperation
from typing import Union

# Pin precision once at import time. 28 significant digits is the decimal
# default but we pin it explicitly so imports elsewhere cannot have widened
# or narrowed the working context before money math runs.
decimal.getcontext().prec = 28

Number = Union[int, float, str, Decimal]
_ZERO = Decimal("0")


def decimal_from_float(x: float) -> Decimal:
    """Convert via str() so 0.1+0.2-style artifacts don't leak in.

    Decimal(0.1) == Decimal('0.1000000000000000055511151231257827...') because
    the float itself is binary; Decimal(str(0.1)) == Decimal('0.1') because
    str() gives back the shortest repr that round-trips.
    """
    return Decimal(str(x))


def _dec(x: Number) -> Decimal:
    """Normalize any supported numeric input to Decimal (float-safe)."""
    if isinstance(x, float):
        return decimal_from_float(x)
    try:
        return Decimal(x)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"not convertible to Decimal: {x!r}") from exc


def quantize_to_step(value: Number, step_size: Number) -> Decimal:
    """Quantize ``value`` DOWN to a multiple of ``step_size``.

    Exchange convention (matches ccxt's ``amount_to_precision``): truncate
    toward zero — ``decimal.ROUND_DOWN`` — so the result never exceeds the
    requested magnitude and always sits on the venue's grid. For positive
    quantities/prices this is plain round-down; for negatives it truncates
    toward zero rather than flooring further away.

    Raises ValueError for non-positive step sizes or non-finite inputs.
    """
    step = _dec(step_size)
    if not step.is_finite() or step <= _ZERO:
        raise ValueError(f"step_size must be > 0, got {step_size!r}")
    val = _dec(value)
    if not val.is_finite():
        raise ValueError(f"value must be finite, got {value!r}")
    return (val / step).to_integral_value(rounding=decimal.ROUND_DOWN) * step


def check_min_notional(
    quantity: Number,
    price: Number,
    min_notional: Number,
) -> bool:
    """Return True iff quantity * price >= min_notional (exact Decimal math).

    Boundary-exact by construction: no float multiplication happens anywhere,
    so qty * price landing precisely on min_notional passes.
    """
    floor = _dec(min_notional)
    if floor < _ZERO:
        raise ValueError(f"min_notional must be >= 0, got {min_notional!r}")
    return _dec(quantity) * _dec(price) >= floor


__all__ = [
    "check_min_notional",
    "decimal_from_float",
    "quantize_to_step",
]
