"""Single-source R-multiple accounting.

Two DISTINCT quantities that must never be conflated:

* :func:`intended_r` — the plan-level multiple measured from ARRIVAL price
  (what the strategy intended when the signal fired).
* :func:`realized_r` — the account-level multiple measured from actual fill
  VWAP minus fees (what the trader actually got).

The known drift case: a trade whose arrival-based math says ~5R can report
~4.17R once entry slippage and fees are charged against the realized leg.
Both numbers are legitimate; they answer different questions and both are
pinned by tests so any future change that silently merges them fails loudly.

TODO(P1-A call-site migration): risk engine / journaling still compute R
ad-hoc; rewiring them onto these functions is a tracked follow-up.
"""

from __future__ import annotations

import decimal
from decimal import Decimal
from typing import Union

# Same pinned context as trading.core.money (28 significant digits).
decimal.getcontext().prec = 28

Number = Union[int, float, str, Decimal]
_ZERO = Decimal("0")


def _dec(x: Number) -> Decimal:
    """Float-safe conversion (str() round-trip), mirroring core.money."""
    if isinstance(x, float):
        return Decimal(str(x))
    return Decimal(x)


def _risk_per_unit(initial_stop: Number, reference_price: Number) -> Decimal:
    stop = _dec(initial_stop)
    ref = _dec(reference_price)
    risk = abs(ref - stop)
    if risk == _ZERO:
        raise ValueError(
            "initial_stop equals reference price; R-multiple undefined "
            f"(stop={stop}, ref={ref})"
        )
    return risk


def _side_sign(reference_price: Decimal, initial_stop: Number) -> int:
    """Infer trade side from stop placement: stop below ref => long (+1),
    stop above ref => short (-1)."""
    return 1 if reference_price - _dec(initial_stop) >= _ZERO else -1


def intended_r(
    arrival_price: Number, initial_stop: Number, exit_price: Number
) -> Decimal:
    """Plan-level R multiple measured from ARRIVAL price, no costs.

    Side is inferred from the stop: stop below arrival => long
    ((exit - arrival) / |arrival - stop|); stop above arrival => short
    ((arrival - exit) / |arrival - stop|). Profitable trades are positive R
    regardless of side. Raises ValueError when stop == arrival (zero risk).
    """
    arrival = _dec(arrival_price)
    risk = _risk_per_unit(initial_stop, arrival)
    side = _side_sign(arrival, initial_stop)
    return (side * (_dec(exit_price) - arrival)) / risk


def realized_r(
    fill_vwap: Number,
    fees_paid: Number,
    initial_stop: Number,
    exit_price: Number,
) -> Decimal:
    """Account-level R multiple measured from actual FILL VWAP, fees deducted.

    Side is inferred from the stop relative to the fill: stop below fill =>
    long ((exit - fees - fill_vwap) / |fill_vwap - stop|); stop above fill =>
    short ((fill_vwap - exit - fees) / |fill_vwap - stop|). Fees are quoted
    in quote currency per unit of the position (callers holding total fees
    should divide by filled size before calling).
    """
    vwap = _dec(fill_vwap)
    risk = _risk_per_unit(initial_stop, vwap)
    side = _side_sign(vwap, initial_stop)
    net_exit = _dec(exit_price) - _dec(fees_paid)
    return (side * (net_exit - vwap)) / risk


__all__ = ["intended_r", "realized_r"]
