"""Backtest market impact wrapper."""

from trading.execution.tca import calculate_market_impact

__all__ = ["apply_market_impact"]


def apply_market_impact(
    order_notional: float,
    adv_notional: float,
    sigma: float = 0.02,
    max_participation_pct: float = 0.03,
) -> tuple[float, float, bool]:
    """Applies market impact law to backtest fills."""
    return calculate_market_impact(
        order_notional=order_notional,
        adv_notional=adv_notional,
        sigma=sigma,
        max_participation_pct=max_participation_pct,
    )
