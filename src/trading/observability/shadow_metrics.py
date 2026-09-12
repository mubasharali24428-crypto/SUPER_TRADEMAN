"""Observability metrics tracker for Shadow Mode execution.

Also hosts the shared statistics primitives for Shadow-campaign evaluation so
expectation/std-dev scaling is defined in exactly ONE place:

- ``per_day_expectation``: campaign-level backtest expectation divided by the
  number of elapsed days (the per-day mean under H0).
- ``per_day_std_dev``: campaign-level std dev converted to per-day scale via
  sqrt(days) — the same law of variance used to build the expectation. Mixing
  a per-day expectation with a campaign-level std (or vice versa) silently
  inflates/deflates every z-score; both sides must scale together.
- ``lower_tail_breach``: ONE-SIDED breach test. A day only breaches when it
  UNDERPERFORMS the expectation beyond the threshold. Days that outperform
  are good news, not breaches.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List

__all__ = [
    "ShadowMetricRecord",
    "ShadowMetricsTracker",
    "per_day_expectation",
    "per_day_std_dev",
    "daily_z_score",
    "lower_tail_breach",
]


@dataclass(frozen=True)
class ShadowMetricRecord:
    signal_price: float
    shadow_fill_price: float
    slippage_pct: float
    latency_ms: float


def per_day_expectation(campaign_expected_pnl_pct: float, days_count: int) -> float:
    """Per-day expected pnl = campaign expectation / number of days."""
    if days_count <= 0:
        return 0.0
    return float(campaign_expected_pnl_pct) / float(days_count)


def per_day_std_dev(campaign_std_dev: float, days_count: int) -> float:
    """Per-day std = campaign std / sqrt(days) — matches the expectation's scaling."""
    if days_count <= 0:
        return 0.0
    return float(campaign_std_dev) / math.sqrt(float(days_count))


def daily_z_score(
    day_pnl_pct: float,
    campaign_expected_pnl_pct: float,
    campaign_std_dev: float,
    days_count: int,
) -> float:
    """z-score of one day against the per-day null hypothesis (both scales agree)."""
    std = per_day_std_dev(campaign_std_dev, days_count)
    if std <= 0.0:
        return 0.0
    return (
        float(day_pnl_pct) - per_day_expectation(campaign_expected_pnl_pct, days_count)
    ) / std


def lower_tail_breach(z: float, threshold: float) -> bool:
    """One-sided lower-tail test: breach ONLY when z < -threshold (underperformance)."""
    return float(z) < -abs(float(threshold))


class ShadowMetricsTracker:
    """Tracks latency drag and execution slippage delta in Shadow Mode."""

    def __init__(self):
        self.records: List[ShadowMetricRecord] = []

    def record_shadow_trade(
        self, signal_price: float, shadow_fill_price: float, latency_ms: float
    ) -> ShadowMetricRecord:
        slippage_pct = (
            abs(shadow_fill_price - signal_price) / signal_price
            if signal_price > 0
            else 0.0
        )
        rec = ShadowMetricRecord(
            signal_price=signal_price,
            shadow_fill_price=shadow_fill_price,
            slippage_pct=slippage_pct,
            latency_ms=latency_ms,
        )
        self.records.append(rec)
        return rec

    def get_summary(self) -> Dict[str, Any]:
        if not self.records:
            return {
                "total_shadow_trades": 0,
                "avg_slippage_pct": 0.0,
                "max_slippage_pct": 0.0,
                "avg_latency_ms": 0.0,
                "max_latency_ms": 0.0,
            }

        slippages = [r.slippage_pct for r in self.records]
        latencies = [r.latency_ms for r in self.records]

        return {
            "total_shadow_trades": len(self.records),
            "avg_slippage_pct": float(sum(slippages) / len(slippages)),
            "max_slippage_pct": float(max(slippages)),
            "avg_latency_ms": float(sum(latencies) / len(latencies)),
            "max_latency_ms": float(max(latencies)),
        }
