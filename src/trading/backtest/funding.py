"""Perpetual funding rate calculations and cash-flow accounting for backtests."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Tuple

from trading.risk.models import AccountState, Side

__all__ = ["FundingEvent", "apply_funding"]


@dataclass(frozen=True)
class FundingEvent:
    timestamp: datetime
    symbol: str
    funding_rate: float  # e.g., 0.0001 (0.01%)
    mark_price: float


def apply_funding(
    account_state: AccountState,
    open_trades: Dict[str, Dict[str, Any]],
    funding_event: FundingEvent,
) -> Tuple[AccountState, float]:
    """Applies funding payments to open perpetual positions.

    Funding Cash Flow = -1 * Side_Multiplier * Position_Notional * Funding_Rate
    where:
      - Long positions (Side_Multiplier = +1) pay when funding_rate > 0.
      - Short positions (Side_Multiplier = -1) receive when funding_rate > 0.

    Returns:
      - Updated AccountState instance with adjusted equity.
      - Total net funding fee paid/received (positive = fee paid, negative = received).
    """
    asset = funding_event.symbol
    if asset not in open_trades:
        return account_state, 0.0

    trade = open_trades[asset]
    side = trade["side"]
    position_size = trade["position_size"]  # base currency units
    notional = position_size * funding_event.mark_price

    side_sign = 1.0 if side is Side.LONG else -1.0
    funding_fee = side_sign * notional * funding_event.funding_rate

    # Cash flow to account is -1 * funding_fee
    new_equity = account_state.equity - funding_fee
    new_peak = max(account_state.peak_equity, new_equity)

    account_state.equity = new_equity
    account_state.peak_equity = new_peak

    return account_state, funding_fee


class PortfolioFundingTracker:
    """Aggregates cumulative portfolio funding cash flows and monitors daily burn rate."""

    def __init__(self, max_daily_funding_burn_pct: float = 0.005):
        self.max_daily_funding_burn_pct = max_daily_funding_burn_pct
        self.funding_history: list[Tuple[datetime, str, float]] = []

    def record_funding(self, event_time: datetime, symbol: str, net_fee: float) -> None:
        self.funding_history.append((event_time, symbol, net_fee))

    def calculate_daily_burn_rate(self, equity: float) -> float:
        """Calculates expected daily funding burn rate as a fraction of account equity."""
        if equity <= 0 or not self.funding_history:
            return 0.0
        # Sum recent 24-hour funding fees (or last 24 entries)
        recent_fees = sum(fee for _, _, fee in self.funding_history[-24:])
        return float(recent_fees / equity)

    def is_funding_burn_excessive(self, equity: float) -> bool:
        return self.calculate_daily_burn_rate(equity) > self.max_daily_funding_burn_pct

