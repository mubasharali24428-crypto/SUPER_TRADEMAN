"""Deployment metrics, drill results, and reconciliation persistence schema and store."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading.observability.logger import get_logger

__all__ = [
    "DeploymentMetricRecord",
    "DrillResultRecord",
    "ReconciliationReportRecord",
    "DeploymentMetricsStore",
    "INIT_OPS_SCHEMA_SQL",
]

logger = get_logger("trading.ops.deployment_metrics")

INIT_OPS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deployment_metrics (
    id BIGSERIAL PRIMARY KEY,
    metric_date DATE NOT NULL,
    execution_mode TEXT NOT NULL,
    symbols TEXT NOT NULL,
    signals_generated INTEGER DEFAULT 0,
    signals_approved INTEGER DEFAULT 0,
    signals_rejected_capital INTEGER DEFAULT 0,
    signals_rejected_stale_data INTEGER DEFAULT 0,
    signals_rejected_portfolio_risk INTEGER DEFAULT 0,
    signals_rejected_liquidity INTEGER DEFAULT 0,
    shadow_fills_generated INTEGER DEFAULT 0,
    liquidity_deficit_events INTEGER DEFAULT 0,
    liquidity_deficit_pct NUMERIC DEFAULT 0.0,
    avg_signal_to_fill_latency_ms NUMERIC DEFAULT 0.0,
    p95_signal_to_fill_latency_ms NUMERIC DEFAULT 0.0,
    p99_signal_to_fill_latency_ms NUMERIC DEFAULT 0.0,
    avg_shadow_slippage_bps NUMERIC DEFAULT 0.0,
    p95_shadow_slippage_bps NUMERIC DEFAULT 0.0,
    staleness_circuit_breaker_trips INTEGER DEFAULT 0,
    websocket_disconnect_events INTEGER DEFAULT 0,
    out_of_order_tick_events INTEGER DEFAULT 0,
    data_quality_failures INTEGER DEFAULT 0,
    shadow_pnl NUMERIC DEFAULT 0.0,
    shadow_pnl_pct NUMERIC DEFAULT 0.0,
    max_shadow_drawdown_pct NUMERIC DEFAULT 0.0,
    portfolio_exposure_pct NUMERIC DEFAULT 0.0,
    effective_leverage NUMERIC DEFAULT 0.0,
    portfolio_volatility_annualized NUMERIC DEFAULT 0.0,
    portfolio_var_95 NUMERIC DEFAULT 0.0,
    portfolio_var_99 NUMERIC DEFAULT 0.0,
    funding_burn_pct_daily NUMERIC DEFAULT 0.0,
    reconciliation_runs INTEGER DEFAULT 0,
    reconciliation_mismatches INTEGER DEFAULT 0,
    quarantine_events INTEGER DEFAULT 0,
    unknown_order_events INTEGER DEFAULT 0,
    position_mismatch_events INTEGER DEFAULT 0,
    balance_mismatch_events INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (metric_date, execution_mode, symbols)
);

CREATE TABLE IF NOT EXISTS drill_results (
    id BIGSERIAL PRIMARY KEY,
    drill_name TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    events_observed JSONB,
    invariant_violations JSONB,
    notes JSONB,
    duration_ms INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reconciliation_reports (
    id BIGSERIAL PRIMARY KEY,
    reconciliation_id TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    execution_mode TEXT NOT NULL,
    positions_match BOOLEAN DEFAULT TRUE,
    balances_match BOOLEAN DEFAULT TRUE,
    open_orders_match BOOLEAN DEFAULT TRUE,
    fills_match BOOLEAN DEFAULT TRUE,
    quarantine_count INTEGER DEFAULT 0,
    unknown_order_count INTEGER DEFAULT 0,
    orphan_fill_count INTEGER DEFAULT 0,
    position_mismatch_count INTEGER DEFAULT 0,
    balance_mismatch_count INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    report_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    alert_id TEXT NOT NULL,
    alert_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    channel TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@dataclass
class AlertRecord:
    alert_id: str
    alert_name: str
    severity: str
    message: str
    channel: str = "slack"
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))



@dataclass
class DeploymentMetricRecord:
    metric_date: str
    execution_mode: str
    symbols: str
    signals_generated: int = 0
    signals_approved: int = 0
    signals_rejected_capital: int = 0
    signals_rejected_stale_data: int = 0
    signals_rejected_portfolio_risk: int = 0
    signals_rejected_liquidity: int = 0
    shadow_fills_generated: int = 0
    liquidity_deficit_events: int = 0
    liquidity_deficit_pct: float = 0.0
    avg_signal_to_fill_latency_ms: float = 0.0
    p95_signal_to_fill_latency_ms: float = 0.0
    p99_signal_to_fill_latency_ms: float = 0.0
    avg_shadow_slippage_bps: float = 0.0
    p95_shadow_slippage_bps: float = 0.0
    staleness_circuit_breaker_trips: int = 0
    websocket_disconnect_events: int = 0
    out_of_order_tick_events: int = 0
    data_quality_failures: int = 0
    shadow_pnl: float = 0.0
    shadow_pnl_pct: float = 0.0
    max_shadow_drawdown_pct: float = 0.0
    portfolio_exposure_pct: float = 0.0
    effective_leverage: float = 0.0
    portfolio_volatility_annualized: float = 0.0
    portfolio_var_95: float = 0.0
    portfolio_var_99: float = 0.0
    funding_burn_pct_daily: float = 0.0
    reconciliation_runs: int = 0
    reconciliation_mismatches: int = 0
    quarantine_events: int = 0
    unknown_order_events: int = 0
    position_mismatch_events: int = 0
    balance_mismatch_events: int = 0


@dataclass
class DrillResultRecord:
    drill_name: str
    execution_mode: str
    status: str
    events_observed: List[str] = field(default_factory=list)
    invariant_violations: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    duration_ms: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ReconciliationReportRecord:
    reconciliation_id: str
    timestamp_utc: datetime
    execution_mode: str
    positions_match: bool = True
    balances_match: bool = True
    open_orders_match: bool = True
    fills_match: bool = True
    quarantine_count: int = 0
    unknown_order_count: int = 0
    orphan_fill_count: int = 0
    position_mismatch_count: int = 0
    balance_mismatch_count: int = 0
    status: str = "CLEAN"
    report_json: Dict[str, Any] = field(default_factory=dict)


class DeploymentMetricsStore:
    """In-memory and DB persistence manager for operational deployment records."""

    def __init__(self):
        self.metrics_history: List[DeploymentMetricRecord] = []
        self.drill_history: List[DrillResultRecord] = []
        self.reconciliation_history: List[ReconciliationReportRecord] = []

    def record_metrics(self, record: DeploymentMetricRecord) -> None:
        self.metrics_history.append(record)
        logger.info(f"[METRICS_RECORDED] Date {record.metric_date}, Mode {record.execution_mode}, Signals {record.signals_generated}")

    def record_drill_result(self, record: DrillResultRecord) -> None:
        self.drill_history.append(record)
        logger.info(f"[DRILL_RESULT_RECORDED] Drill {record.drill_name}, Status {record.status}")

    def record_reconciliation_report(self, record: ReconciliationReportRecord) -> None:
        self.reconciliation_history.append(record)
        logger.info(f"[RECONCILIATION_REPORT_RECORDED] ID {record.reconciliation_id}, Status {record.status}")

    def get_cumulative_metrics(self, days: int = 20) -> Optional[DeploymentMetricRecord]:
        if not self.metrics_history:
            return None
        recent = self.metrics_history[-days:]
        aggregated = DeploymentMetricRecord(
            metric_date=recent[-1].metric_date,
            execution_mode=recent[-1].execution_mode,
            symbols=recent[-1].symbols,
            signals_generated=sum(r.signals_generated for r in recent),
            signals_approved=sum(r.signals_approved for r in recent),
            signals_rejected_capital=sum(r.signals_rejected_capital for r in recent),
            signals_rejected_stale_data=sum(r.signals_rejected_stale_data for r in recent),
            signals_rejected_portfolio_risk=sum(r.signals_rejected_portfolio_risk for r in recent),
            signals_rejected_liquidity=sum(r.signals_rejected_liquidity for r in recent),
            shadow_fills_generated=sum(r.shadow_fills_generated for r in recent),
            liquidity_deficit_events=sum(r.liquidity_deficit_events for r in recent),
            liquidity_deficit_pct=sum(r.liquidity_deficit_pct for r in recent) / len(recent),
            avg_signal_to_fill_latency_ms=sum(r.avg_signal_to_fill_latency_ms for r in recent) / len(recent),
            p95_signal_to_fill_latency_ms=max(r.p95_signal_to_fill_latency_ms for r in recent),
            p99_signal_to_fill_latency_ms=max(r.p99_signal_to_fill_latency_ms for r in recent),
            avg_shadow_slippage_bps=sum(r.avg_shadow_slippage_bps for r in recent) / len(recent),
            p95_shadow_slippage_bps=max(r.p95_shadow_slippage_bps for r in recent),
            staleness_circuit_breaker_trips=sum(r.staleness_circuit_breaker_trips for r in recent),
            websocket_disconnect_events=sum(r.websocket_disconnect_events for r in recent),
            out_of_order_tick_events=sum(r.out_of_order_tick_events for r in recent),
            data_quality_failures=sum(r.data_quality_failures for r in recent),
            shadow_pnl=sum(r.shadow_pnl for r in recent),
            shadow_pnl_pct=sum(r.shadow_pnl_pct for r in recent),
            max_shadow_drawdown_pct=max(r.max_shadow_drawdown_pct for r in recent),
            portfolio_exposure_pct=recent[-1].portfolio_exposure_pct,
            effective_leverage=recent[-1].effective_leverage,
            portfolio_volatility_annualized=recent[-1].portfolio_volatility_annualized,
            portfolio_var_95=recent[-1].portfolio_var_95,
            portfolio_var_99=recent[-1].portfolio_var_99,
            funding_burn_pct_daily=recent[-1].funding_burn_pct_daily,
            reconciliation_runs=sum(r.reconciliation_runs for r in recent),
            reconciliation_mismatches=sum(r.reconciliation_mismatches for r in recent),
            quarantine_events=sum(r.quarantine_events for r in recent),
            unknown_order_events=sum(r.unknown_order_events for r in recent),
            position_mismatch_events=sum(r.position_mismatch_events for r in recent),
            balance_mismatch_events=sum(r.balance_mismatch_events for r in recent),
        )
        return aggregated
