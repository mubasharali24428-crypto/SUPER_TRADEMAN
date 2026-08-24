"""initial schema: order_intent, decisions, ohlcv, funding_rates, audit_events, alerts, ops tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-24

Schema source of truth. DDL below is copied VERBATIM from the runtime CREATE
TABLE statements that previously lived in:

- src/trading/execution/outbox.py   (order_intent)
- src/trading/journal.py            (decisions)
- src/trading/data/crypto.py        (ohlcv, funding_rates)
- src/trading/ops/deployment_metrics.py INIT_OPS_SCHEMA_SQL
                                    (alerts, deployment_metrics, drill_results,
                                     reconciliation_reports)

Documented deviations:
- decisions gains ``decision_id TEXT NOT NULL UNIQUE`` (idempotency key for
  journal.store_decisions INSERT .. ON CONFLICT).
- audit_events had no prior SQL DDL anywhere; it is modeled directly from the
  AuditRecord dataclass in src/trading/security/audit_ledger.py.

Revision ID: 0001_initial
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- order_intent (verbatim from execution/outbox.py ensure_schema) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS order_intent (
            client_order_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            stop_price DOUBLE PRECISION NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL,
            exchange_order_id TEXT
        )
        """
    )

    # --- decisions (verbatim from journal.py ensure_schema) ---
    # Deviation: decision_id TEXT NOT NULL UNIQUE added as the idempotency key.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id BIGSERIAL PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            asset TEXT NOT NULL,
            entry_time TIMESTAMPTZ NOT NULL,
            side TEXT NOT NULL,
            decision TEXT NOT NULL,
            expected_reward_risk DOUBLE PRECISION NOT NULL,
            consequence_r_multiple DOUBLE PRECISION NOT NULL,
            consequence_net_pnl DOUBLE PRECISION NOT NULL,
            exit_reason TEXT NOT NULL,
            verdict TEXT NOT NULL
        )
        """
    )

    # --- ohlcv + funding_rates (verbatim from data/crypto.py ensure_schema) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv (
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (exchange, symbol, timeframe, timestamp)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS funding_rates (
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            funding_rate DOUBLE PRECISION NOT NULL,
            mark_price DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (exchange, symbol, timestamp)
        )
        """
    )

    # --- audit_events (modeled from security/audit_ledger.AuditRecord;
    #     no prior SQL DDL existed) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            record_id TEXT PRIMARY KEY,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            event_type TEXT NOT NULL,
            payload_json JSONB NOT NULL,
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL
        )
        """
    )

    # --- alerts / deployment_metrics / drill_results / reconciliation_reports
    #     (verbatim from ops/deployment_metrics.py INIT_OPS_SCHEMA_SQL) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id BIGSERIAL PRIMARY KEY,
            alert_id TEXT NOT NULL,
            alert_name TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            channel TEXT NOT NULL,
            timestamp_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reconciliation_reports")
    op.execute("DROP TABLE IF EXISTS drill_results")
    op.execute("DROP TABLE IF EXISTS deployment_metrics")
    op.execute("DROP TABLE IF EXISTS alerts")
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS funding_rates")
    op.execute("DROP TABLE IF EXISTS ohlcv")
    op.execute("DROP TABLE IF EXISTS decisions")
    op.execute("DROP TABLE IF EXISTS order_intent")
