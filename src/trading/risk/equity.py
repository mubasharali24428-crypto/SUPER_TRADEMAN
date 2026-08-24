"""Canonical equity accounting for the shared-account book.

One definition of what the account is worth RIGHT NOW -- settled (realized)
cash equity plus unrealized P&L evaluated at mark prices -- so drawdown gates,
circuit breakers, and reporting can never disagree by reading different
series. All values are plain floats; Decimal is deliberately kept out of this
hot path.

The ``daily_pnl_pct`` denominator is defined exactly once, in
``trading.risk.models.day_start_equity``; it lives there (not here) because
this module already imports ``models`` for the account/position types and the
reverse import would be circular. It is re-exported below so callers have a
single import site for all equity-basis questions.
"""
from trading.risk.models import (
    AccountState,
    Position,
    Side,
    day_start_equity,  # noqa: F401  (re-export: THE daily_pnl_pct denominator)
)

__all__ = ["marked_equity", "unrealized_pnl", "day_start_equity"]


def unrealized_pnl(positions: list[Position], mark_prices: dict[str, float]) -> float:
    """Sum of unrealized P&L across open positions at the given mark prices.

    An asset missing from ``mark_prices`` is marked at its own entry price
    (zero unrealized), mirroring ``check_portfolio_liquidation`` in models.py.
    """
    total = 0.0
    for pos in positions:
        entry = pos.entry_price
        mark = mark_prices.get(pos.asset, entry)
        size = getattr(pos, "position_size", 1.0)
        if pos.side is Side.LONG:
            total += size * (mark - entry)
        else:
            total += size * (entry - mark)
    return total


def marked_equity(account: AccountState, mark_prices: dict[str, float]) -> float:
    """One equity-curve point: settled_equity + sum(unrealized at mark).

    ``account.equity`` is the settled (realized-PnL-only) balance; the open
    positions are marked to ``mark_prices`` on top of it. This is the series
    the portfolio backtester's curve, drawdown checks, and breakers all read.
    """
    return account.equity + unrealized_pnl(account.open_positions, mark_prices)
