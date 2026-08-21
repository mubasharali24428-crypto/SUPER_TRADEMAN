# Monitoring & Telemetry Setup Guide — SUPER_TRADEMAN

## 1. Overview
`SUPER_TRADEMAN` provides full Prometheus, Grafana, Alertmanager, and Loki telemetry integration for continuous quantitative oversight.

## 2. Docker Compose Infrastructure
Launch the complete monitoring stack locally or on production servers:

```bash
cd monitoring
docker-compose up -d
```

### Stack Components:
- **Prometheus** (`http://localhost:9090`): Collects metric time-series from `/metrics`.
- **Grafana** (`http://localhost:3000`): Visualizes performance, latency, fill rates, and PnL. Default login: `admin` / `admin`.
- **Alertmanager** (`http://localhost:9093`): Routes alerts to Slack, PagerDuty, and Email.
- **Loki** (`http://localhost:3100`): Aggregates structured JSON log streams.

## 3. Metrics Exposition
`MetricsCollector` (`src/trading/ops/metrics_collector.py`) exposes standard metrics:
- `super_trademan_signals_total`: Total trading signals generated.
- `super_trademan_fills_total`: Total synthetic/live fills executed.
- `super_trademan_latency_p95_ms` / `p99_ms`: Signal-to-fill 95th/99th percentile latencies.
- `super_trademan_shadow_pnl_pct`: Cumulative Shadow PnL percentage.
- `super_trademan_max_drawdown_pct`: Portfolio peak drawdown percentage.
- `super_trademan_staleness_trips`: Staleness sentinel circuit breaker trip count.

## 4. Grafana Dashboard Import
1. Log into Grafana at `http://localhost:3000`.
2. Navigate to **Dashboards** -> **Import**.
3. Upload `monitoring/grafana/dashboards/super_trademan.json`.
4. Select the Prometheus data source and click **Import**.
