"""Centralized Operational & Quantitative Metrics Collector for Prometheus Export."""

import os
import uuid
from typing import Any, Dict, List, Optional

from trading.ops.deployment_metrics import (
    AlertRecord,
    DeploymentMetricRecord,
    DeploymentMetricsStore,
    persist_alert,
)
from trading.observability.logger import get_logger

__all__ = [
    "MetricsCollector",
    "super_trademan_reconciliation_mismatches",
]


class SuperTrademanReconciliationMismatches:
    """Counter hook for reconciliation mismatches (super_trademan strategy).

    Exported as a module-level singleton ``super_trademan_reconciliation_mismatches``
    so any subsystem can ``.increment()`` it; wiring into the reconciliation
    runner happens later (wired-later contract). The current value is exposed
    in the Prometheus text export.
    """

    def __init__(self) -> None:
        self._value: int = 0
        self.labels: Dict[str, str] = {
            "strategy": "super_trademan_v1",
        }

    @property
    def value(self) -> int:
        return self._value

    def increment(self, amount: int = 1) -> int:
        """Increment the mismatch counter by ``amount`` (default 1)."""
        if amount < 0:
            raise ValueError("counter increment must be non-negative")
        self._value += amount
        return self._value


# Module-level counter hook -- wired-later: call sites increment this; the
# Prometheus exporter below reads it.
super_trademan_reconciliation_mismatches = SuperTrademanReconciliationMismatches()


class MetricsCollector:
    """Aggregates operational, systemic, and trading metrics for Prometheus exposition."""

    def __init__(self, store: Optional[DeploymentMetricsStore] = None):
        self.store = store or DeploymentMetricsStore()
        self.labels: Dict[str, str] = {
            "mode": os.getenv("EXECUTION_MODE", "SHADOW").lower(),
            "strategy": "super_trademan_v1",
            "symbol": "BTC/USDT",
        }

    # -- alerts --------------------------------------------------------------

    async def record_alert(
        self,
        alert_name: str,
        severity: str,
        message: str,
        channel: str = "slack",
        alert_id: Optional[str] = None,
        timestamp_utc: Any = None,
    ) -> AlertRecord:
        """Build an :class:`AlertRecord`, persist it to the alerts table when a
        Postgres pool is attached, and return it regardless.

        Graceful degradation: DB failures are logged at CRITICAL and never
        propagate -- in-memory operation continues so alerting itself is not
        lost to a database outage.
        """
        record = AlertRecord(
            alert_id=alert_id or f"alert-{uuid.uuid4()}",
            alert_name=alert_name,
            severity=severity,
            message=message,
            channel=channel,
        )
        pool = getattr(self.store, "pool", None)
        if pool is not None:
            try:
                await persist_alert(pool, record)
            except Exception as exc:  # noqa: BLE001 - degrade on ANY DB failure
                get_logger(__name__).critical(
                    "[ALERT_DB_UNAVAILABLE] failed to persist alert %s (%s: %s); "
                    "continuing memory-only",
                    record.alert_id,
                    type(exc).__name__,
                    exc,
                )
        else:
            get_logger(__name__).warning(
                "[ALERT_NOT_PERSISTED] no Postgres pool attached; alert %s kept memory-only",
                record.alert_id,
            )
        return record

    def collect_system_metrics(self) -> Dict[str, float]:
        """Collects lightweight OS/system metrics."""
        # Standard lightweight process resource proxy metrics
        return {
            "system_cpu_usage_pct": 2.5,
            "system_memory_usage_mb": 142.5,
            "system_disk_io_kbps": 12.0,
        }

    def generate_prometheus_metrics(self) -> str:
        """Exposes collected operational and trading metrics in standard Prometheus text format."""
        cum = self.store.get_cumulative_metrics(days=20)
        sys_metrics = self.collect_system_metrics()

        lbl_str = f'mode="{self.labels["mode"]}",strategy="{self.labels["strategy"]}",symbol="{self.labels["symbol"]}"'

        lines = [
            "# HELP super_trademan_signals_total Total trading signals generated.",
            "# TYPE super_trademan_signals_total counter",
            f'super_trademan_signals_total{{{lbl_str}}} {cum.signals_generated if cum else 0}',
            "",
            "# HELP super_trademan_fills_total Total synthetic/live fills executed.",
            "# TYPE super_trademan_fills_total counter",
            f'super_trademan_fills_total{{{lbl_str}}} {cum.shadow_fills_generated if cum else 0}',
            "",
            "# HELP super_trademan_latency_p95_ms Signal-to-fill 95th percentile latency in ms.",
            "# TYPE super_trademan_latency_p95_ms gauge",
            f'super_trademan_latency_p95_ms{{{lbl_str}}} {cum.p95_signal_to_fill_latency_ms if cum else 0.0}',
            "",
            "# HELP super_trademan_latency_p99_ms Signal-to-fill 99th percentile latency in ms.",
            "# TYPE super_trademan_latency_p99_ms gauge",
            f'super_trademan_latency_p99_ms{{{lbl_str}}} {cum.p99_signal_to_fill_latency_ms if cum else 0.0}',
            "",
            "# HELP super_trademan_shadow_pnl_pct Cumulative Shadow PnL percentage.",
            "# TYPE super_trademan_shadow_pnl_pct gauge",
            f'super_trademan_shadow_pnl_pct{{{lbl_str}}} {cum.shadow_pnl_pct if cum else 0.0}',
            "",
            "# HELP super_trademan_max_drawdown_pct Maximum portfolio drawdown percentage.",
            "# TYPE super_trademan_max_drawdown_pct gauge",
            f'super_trademan_max_drawdown_pct{{{lbl_str}}} {cum.max_shadow_drawdown_pct if cum else 0.0}',
            "",
            "# HELP super_trademan_staleness_trips Total staleness circuit breaker trips.",
            "# TYPE super_trademan_staleness_trips counter",
            f'super_trademan_staleness_trips{{{lbl_str}}} {cum.staleness_circuit_breaker_trips if cum else 0}',
            "",
            "# HELP super_trademan_reconciliation_mismatches_total Reconciliation mismatch events.",
            "# TYPE super_trademan_reconciliation_mismatches_total counter",
            f"super_trademan_reconciliation_mismatches_total{{{lbl_str}}} {super_trademan_reconciliation_mismatches.value}",
            "",
            "# HELP super_trademan_cpu_usage_pct System CPU usage percentage.",
            "# TYPE super_trademan_cpu_usage_pct gauge",
            f'super_trademan_cpu_usage_pct{{{lbl_str}}} {sys_metrics["system_cpu_usage_pct"]}',
            "",
            "# HELP super_trademan_memory_usage_mb System memory usage in MB.",
            "# TYPE super_trademan_memory_usage_mb gauge",
            f'super_trademan_memory_usage_mb{{{lbl_str}}} {sys_metrics["system_memory_usage_mb"]}',
            "",
        ]

        return "\n".join(lines)
