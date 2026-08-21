"""Observability metrics tracker for Shadow Mode execution."""

from dataclasses import dataclass, field
from typing import Dict, List, Any

__all__ = ["ShadowMetricRecord", "ShadowMetricsTracker"]


@dataclass(frozen=True)
class ShadowMetricRecord:
    signal_price: float
    shadow_fill_price: float
    slippage_pct: float
    latency_ms: float


class ShadowMetricsTracker:
    """Tracks latency drag and execution slippage delta in Shadow Mode."""

    def __init__(self):
        self.records: List[ShadowMetricRecord] = []

    def record_shadow_trade(
        self, signal_price: float, shadow_fill_price: float, latency_ms: float
    ) -> ShadowMetricRecord:
        slippage_pct = abs(shadow_fill_price - signal_price) / signal_price if signal_price > 0 else 0.0
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
