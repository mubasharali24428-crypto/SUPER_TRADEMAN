"""Deployment metrics, drill results, and reconciliation persistence store.

Schema ownership: the ``deployment_metrics``, ``drill_results``,
``reconciliation_reports`` and ``alerts`` tables are created by Alembic
migrations (alembic/versions/0001_initial). ``INIT_OPS_SCHEMA_SQL`` below is
kept only as a historical reference copy of that DDL -- runtime code must not
execute it.

Persistence model: daily metric rows are UPSERTed into Postgres keyed by the
table's UNIQUE (metric_date, execution_mode, symbols); the in-memory list is
kept ONLY as a read cache. If the database is unavailable the store logs a
CRITICAL message and keeps operating memory-only, so processes (and tests)
without a reachable Postgres still work.
"""

import asyncio
import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import asyncpg

from trading.observability.logger import get_logger

__all__ = [
    "DeploymentMetricRecord",
    "DrillResultRecord",
    "ReconciliationReportRecord",
    "AlertRecord",
    "DeploymentMetricsStore",
    "persist_alert",
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


_METRIC_KEY_COLUMNS = ("metric_date", "execution_mode", "symbols")
_METRIC_VALUE_COLUMNS = (
    "signals_generated",
    "signals_approved",
    "signals_rejected_capital",
    "signals_rejected_stale_data",
    "signals_rejected_portfolio_risk",
    "signals_rejected_liquidity",
    "shadow_fills_generated",
    "liquidity_deficit_events",
    "liquidity_deficit_pct",
    "avg_signal_to_fill_latency_ms",
    "p95_signal_to_fill_latency_ms",
    "p99_signal_to_fill_latency_ms",
    "avg_shadow_slippage_bps",
    "p95_shadow_slippage_bps",
    "staleness_circuit_breaker_trips",
    "websocket_disconnect_events",
    "out_of_order_tick_events",
    "data_quality_failures",
    "shadow_pnl",
    "shadow_pnl_pct",
    "max_shadow_drawdown_pct",
    "portfolio_exposure_pct",
    "effective_leverage",
    "portfolio_volatility_annualized",
    "portfolio_var_95",
    "portfolio_var_99",
    "funding_burn_pct_daily",
    "reconciliation_runs",
    "reconciliation_mismatches",
    "quarantine_events",
    "unknown_order_events",
    "position_mismatch_events",
    "balance_mismatch_events",
)

_ALL_METRIC_COLUMNS = _METRIC_KEY_COLUMNS + _METRIC_VALUE_COLUMNS

UPSERT_DEPLOYMENT_METRICS_SQL = (
    "INSERT INTO deployment_metrics ({cols}) VALUES ({vals}) "
    "ON CONFLICT (metric_date, execution_mode, symbols) DO UPDATE SET {sets}"
).format(
    cols=", ".join(_ALL_METRIC_COLUMNS),
    vals=", ".join(f"${i}" for i in range(1, len(_ALL_METRIC_COLUMNS) + 1)),
    sets=", ".join(f"{c} = EXCLUDED.{c}" for c in _METRIC_VALUE_COLUMNS),
)


def _record_to_row(record: DeploymentMetricRecord) -> tuple:
    return tuple(getattr(record, col) for col in _ALL_METRIC_COLUMNS)


def _schedule_db_write(coro) -> None:
    """Fire-and-forget a DB write on the running loop, swallowing errors.

    Called from sync record_* methods; when no event loop is running (plain
    unit tests, scripts) the coroutine is closed immediately -- memory cache
    remains the source of truth for reads.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)

    def _log_failure(done: asyncio.Task) -> None:
        if not done.cancelled() and done.exception() is not None:
            logger.error(
                "[METRICS_DB_WRITE_FAILED] background Postgres write failed: %s",
                done.exception(),
            )

    task.add_done_callback(_log_failure)


class DeploymentMetricsStore:
    """Postgres-backed persistence with an in-memory read cache.

    Daily metric rows are UPSERTed into ``deployment_metrics`` keyed by the
    table's UNIQUE (metric_date, execution_mode, symbols); drill results and
    reconciliation reports are INSERTed into their tables. The in-memory lists
    remain the read path so consumers never block on the database.

    Degradation contract: any Postgres failure is logged at CRITICAL level and
    the store keeps operating memory-only. Passing ``pool=None`` (the default)
    runs entirely in memory.
    """

    def __init__(self, pool: Optional[asyncpg.Pool] = None):
        self.pool = pool
        self.metrics_history: List[DeploymentMetricRecord] = []
        self.drill_history: List[DrillResultRecord] = []
        self.reconciliation_history: List[ReconciliationReportRecord] = []
        self._db_degraded: Set[str] = set()

    # -- degradation -------------------------------------------------------

    def _degrade(self, op_name: str, exc: Exception) -> None:
        if op_name not in self._db_degraded:
            self._db_degraded.add(op_name)
            logger.critical(
                "[METRICS_DB_UNAVAILABLE] %s failed (%s: %s); continuing "
                "memory-only until Postgres recovers",
                op_name,
                type(exc).__name__,
                exc,
            )
        else:
            logger.warning("[METRICS_DB_UNAVAILABLE] %s still failing: %s", op_name, exc)

    # -- writes ------------------------------------------------------------

    def record_metrics(self, record: DeploymentMetricRecord) -> None:
        self.metrics_history.append(record)
        logger.info(f"[METRICS_RECORDED] Date {record.metric_date}, Mode {record.execution_mode}, Signals {record.signals_generated}")
        if self.pool is not None:
            _schedule_db_write(self._upsert_metric_row(record))

    async def _upsert_metric_row(self, record: DeploymentMetricRecord) -> None:
        try:
            assert self.pool is not None, "upsert requires an attached Postgres pool"
            await self.pool.execute(UPSERT_DEPLOYMENT_METRICS_SQL, *_record_to_row(record))
        except Exception as exc:  # noqa: BLE001 - degrade on ANY DB failure
            self._degrade("deployment_metrics.upsert", exc)

    def record_drill_result(self, record: DrillResultRecord) -> None:
        self.drill_history.append(record)
        logger.info(f"[DRILL_RESULT_RECORDED] Drill {record.drill_name}, Status {record.status}")
        if self.pool is not None:
            _schedule_db_write(self._insert_drill_result(record))

    async def _insert_drill_result(self, record: DrillResultRecord) -> None:
        try:
            assert self.pool is not None, "drill insert requires an attached Postgres pool"
            await self.pool.execute(
                """
                INSERT INTO drill_results (drill_name, execution_mode, status,
                                           events_observed, invariant_violations,
                                           notes, duration_ms, created_at)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8)
                """,
                record.drill_name,
                record.execution_mode,
                record.status,
                _json_list(record.events_observed),
                _json_list(record.invariant_violations),
                _json_list(record.notes),
                record.duration_ms,
                record.created_at,
            )
        except Exception as exc:  # noqa: BLE001 - degrade on ANY DB failure
            self._degrade("drill_results.insert", exc)

    def record_reconciliation_report(self, record: ReconciliationReportRecord) -> None:
        self.reconciliation_history.append(record)
        logger.info(f"[RECONCILIATION_REPORT_RECORDED] ID {record.reconciliation_id}, Status {record.status}")
        if self.pool is not None:
            _schedule_db_write(self._insert_reconciliation_report(record))

    async def _insert_reconciliation_report(self, record: ReconciliationReportRecord) -> None:
        try:
            assert self.pool is not None, "recon insert requires an attached Postgres pool"
            await self.pool.execute(
                """
                INSERT INTO reconciliation_reports (reconciliation_id, timestamp_utc,
                                                    execution_mode, positions_match,
                                                    balances_match, open_orders_match,
                                                    fills_match, quarantine_count,
                                                    unknown_order_count, orphan_fill_count,
                                                    position_mismatch_count,
                                                    balance_mismatch_count, status, report_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb)
                """,
                record.reconciliation_id,
                record.timestamp_utc,
                record.execution_mode,
                record.positions_match,
                record.balances_match,
                record.open_orders_match,
                record.fills_match,
                record.quarantine_count,
                record.unknown_order_count,
                record.orphan_fill_count,
                record.position_mismatch_count,
                record.balance_mismatch_count,
                record.status,
                _json_obj(record.report_json),
            )
        except Exception as exc:  # noqa: BLE001 - degrade on ANY DB failure
            self._degrade("reconciliation_reports.insert", exc)


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


async def persist_alert(pool: asyncpg.Pool, alert: AlertRecord) -> None:
    """Persist an :class:`AlertRecord` row into the alerts table."""
    await pool.execute(
        """
        INSERT INTO alerts (alert_id, alert_name, severity, message, channel, timestamp_utc)
        VALUES ($1, $2, $3, $4, $5, COALESCE($6, NOW()))
        """,
        alert.alert_id,
        alert.alert_name,
        alert.severity,
        alert.message,
        alert.channel,
        alert.timestamp_utc,
    )


def _json_list(items: List[str]) -> str:
    return _json.dumps(list(items))


def _json_obj(obj: Dict[str, Any]) -> str:
    return _json.dumps(obj)
