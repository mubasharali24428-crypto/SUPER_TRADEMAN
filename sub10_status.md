# SUB-10 Status — Observability Truthfulness (repo main @857b038)

Status: COMPLETE. All owned lanes implemented; pytest green.

Scope: health_service.py, logging_config.py, observability/shadow_metrics.py (+ the
breach/z logic that lives in ops/shadow_campaign.py), monitoring/prometheus/alerts.yml
runbook pass, tests. metrics_collector.py untouched (SUB-03 property; read-only).

## Progress log
- [x] Recon: repo at "/Users/user/AG PROJ1/algo-trading-system" (HEAD 857b038 confirmed).
- [x] Read all owned files + shadow_campaign.py (actual home of abs()/z-scale bug), tier_state.py,
      heartbeat.py, config.py ExecutionMode, alerts.yml vs docs/INCIDENT_RESPONSE.md anchors.
- [x] health_service.py: UNKNOWN enum value added; real asyncpg SELECT 1 + measured latency
      (loop-safe via fresh-thread asyncio.run); venue_adapter now REQUIRED positional param,
      MockVenueAdapter auto-selection gated behind simulation-mode check using
      trading.config.ExecutionMode (SIMULATION attr honored if ever added, BACKTEST as the
      simulation mode today); daemon check = heartbeat state-file mtime freshness <2*interval
      (path from RISK_TIER_STATE_FILE/env or param), UNKNOWN when path unknown/missing.
- [x] logging_config.py: contextvars.ContextVar correlation id (set_correlation_id/get_correlation_id),
      CorrelationIdFilter stamps records; JSONFormatter emits ts/severity/service/logger/
      trace_id/message/context keys; RedactionFilter strips postgres:// and postgresql://
      DSNs — credentials, host, path and querystring masked — from message, %-args, and
      exception text (pre-rendered into record.exc_text).
- [x] shadow_metrics.py: added shared primitives per_day_expectation / per_day_std_dev /
      daily_z_score / lower_tail_breach (single source of scaling truth).
- [x] shadow_campaign.py (bug's actual home): per-day z-test now uses per-day expectation AND
      per-day std (sqrt(days) law); breach detection one-sided lower-tail (abs() dropped);
      Gate-1 campaign z-test also one-sided. Outperformance never a breach.
- [x] alerts.yml: all 4 rules carry verified runbook_url anchors (checked against
      docs/INCIDENT_RESPONSE.md headings programmatically); severity vocabulary documented +
      consistent with alert_manager.AlertSeverity (warning/critical/emergency). Exprs untouched.
- [x] Tests: tests/ops/test_health_service.py rewritten (11 tests incl. UNKNOWN-not-HEALTHY,
      real-probe fake-asyncpg latency, adapter gating, daemon mtime fresh/stale/missing);
      tests/ops/test_logging_config.py extended (context-local cid, JSON keys, DSN redaction in
      exception + args); NEW tests/trading/ops/test_shadow_truthfulness.py (one-sided breach,
      outperform-day NOT breach, golden z-scale consistency, campaign-level paths).

## Verification
- .venv/bin/python -m pytest tests/ops/test_health_service.py -q  -> 11 passed
- .venv/bin/python -m pytest tests/trading/ops/test_shadow_truthfulness.py -q -> 8 passed
- .venv/bin/python -m pytest tests/ops/test_logging_config.py -q -> 7 passed
- Regression: tests/ops (whole dir incl. shadow_campaign/shadow_report/alert_manager/metrics_collector)
  -> 65 passed, 0 failed.
- Cross-lane check: src/trading/api/app.py (another lane) already constructs
  HealthService(venue_adapter=..., execution_mode=sim) — compatible with the new contract.

## Enum/status changes
- HealthStatus.UNKNOWN = "UNKNOWN" added to src/trading/ops/health_service.py.
- database component can now return UNKNOWN (no POSTGRES_URL) instead of fabricated HEALTHY/1.2ms;
  daemon returns UNKNOWN when heartbeat path unknown/file missing; CRITICAL on stale heartbeat
  or failed probes; exchange_api CRITICAL on failed live probe, HEALTHY w/ "Simulation" detail
  for mocks.

## Notes / deviations
- The task sheet placed the z-test in observability/shadow_metrics.py; it actually lived in
  ops/shadow_campaign.py::evaluate_campaign_status. Fixed there; primitives live in the owned
  shadow_metrics.py so both files stay consistent.
- ExecutionMode has no SIMULATION member upstream; gating treats BACKTEST as the simulation
  mode via getattr(ExecutionMode, "SIMULATION", ExecutionMode.BACKTEST), honoring a future member.
