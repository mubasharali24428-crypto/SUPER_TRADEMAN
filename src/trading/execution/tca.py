"""Transaction Cost Analysis (TCA) and Market Impact model."""

import math
from typing import Tuple

__all__ = ["calculate_market_impact", "calibrate_sigma"]


def calibrate_sigma(prices: list[float]) -> float:
    """Calibrates daily volatility sigma from a price series."""
    if len(prices) < 2:
        return 0.02  # default 2% daily volatility proxy

    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    mean_ret = sum(log_returns) / len(log_returns)
    var = sum((r - mean_ret) ** 2 for r in log_returns) / len(log_returns)
    sigma = math.sqrt(var)
    return max(0.005, sigma)


def calculate_market_impact(
    order_notional: float,
    adv_notional: float,
    sigma: float = 0.02,
    gamma: float = 0.5,
    max_participation_pct: float = 0.03,
) -> Tuple[float, float, bool]:
    """Calculates market impact percentage and capped order size.

    Formula: impact_pct = gamma * sigma * sqrt(order_notional / adv_notional)

    If participation (order_notional / adv_notional) exceeds max_participation_pct,
    the order is capped at (adv_notional * max_participation_pct) and adjusted=True.

    Returns:
        (impact_pct, executed_notional, was_capped)
    """
    if adv_notional <= 0:
        return 0.001, order_notional, False

    participation = order_notional / adv_notional
    was_capped = False
    executed_notional = order_notional

    if participation > max_participation_pct:
        executed_notional = adv_notional * max_participation_pct
        participation = max_participation_pct
        was_capped = True

    impact_pct = gamma * sigma * math.sqrt(participation)
    return impact_pct, executed_notional, was_capped
