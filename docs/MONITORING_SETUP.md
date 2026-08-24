# Monitoring & Telemetry Setup Guide — SUPER_TRADEMAN

> Rewritten for OT-1. This document describes the files **actually in the
> repository**: `monitoring/docker-compose.yml`,
> `monitoring/prometheus/prometheus.yml`, `monitoring/prometheus/alerts.yml`,
> `monitoring/alertmanager/alertmanager.yml`, `monitoring/loki/loki-config.yaml`
> and the tracing module `src/trading/observability/otel.py`.

## 1. Overview & alert flow

```
FastAPI app (/metrics, Bearer-protected)          JSON logs (stdout + logs/)
        │                                                  │
        │ scrape (Bearer token)                   docker logging driver
        ▼                                                  ▼
   Prometheus ──rules: alerts.yml──► Alertmanager ──webhook──► ALERT_WEBHOOK_URL
        │                                  ▲
        │ scrape                           │ ruler alerts (wired, no rules yet)
        ▼                                  │
      Loki ◄───────────────────────────────┘   Grafana queries Prometheus + Loki
```

Metric → alert → webhook flow in one sentence: **Prometheus evaluates the
recording/alert rules in `prometheus/alerts.yml` every 15s and sends firing
alerts to Alertmanager (`alertmanager:9093`), whose routes forward them as HTTP
POSTs to the endpoint configured via `ALERT_WEBHOOK_URL`** — EMERGENCY alerts
immediately (group_wait 0s, repeat 30m), everything else batched (group_wait
30s, repeat 4h).

## 2. Stack components (all ports bound to 127.0.0.1 ONLY)

| Service | Image (pinned) | Host URL | Purpose |
|---|---|---|---|
| prometheus | prom/prometheus:v2.47.0 | http://127.0.0.1:9090 | Scrapes metrics, evaluates alert rules |
| grafana | grafana/grafana:10.1.0 | http://127.0.0.1:3000 | Dashboards |
| alertmanager | prom/alertmanager:v0.26.0 | http://127.0.0.1:9093 | Alert routing/dedup → webhook |
| loki | grafana/loki:2.9.0 | http://127.0.0.1:3100 | Log aggregation (7-day retention) |

## 3. Required environment variables

Compose **fails closed** (`${VAR:?}` interpolation) — services refuse to start
when a required variable is unset. There are no default credentials anywhere.

| Variable | Required by | Notes |
|---|---|---|
| `GRAFANA_ADMIN_PASSWORD` | grafana | e.g. `openssl rand -base64 32`. Login is `admin` / this value — NOT admin/admin. |
| `METRICS_BEARER_TOKEN` | prometheus (+ your API process) | Same value on BOTH sides: Prometheus writes it to `/etc/prometheus/metrics_token` at start and presents it as a Bearer token; the API's `MetricsAuthMiddleware` compares it against its own `METRICS_BEARER_TOKEN`. |
| `ALERT_WEBHOOK_URL` | alertmanager | Fully qualified webhook URL (e.g. `http://127.0.0.1:9000/hooks/alerts`). sed-injected into both receivers in `alertmanager.yml` at container start. |

Optional: `OTEL_EXPORTER_OTLP_ENDPOINT` (host-side API process) enables real
tracing — see §6.

Provide them via an untracked `.env` next to `monitoring/docker-compose.yml`
(see repo-root `.env.example`) or export them in the shell:

```bash
export GRAFANA_ADMIN_PASSWORD="$(openssl rand -base64 32)"
export METRICS_BEARER_TOKEN="$(openssl rand -hex 32)"
export ALERT_WEBHOOK_URL="https://hooks.example.invalid/trading-alerts"
cd monitoring && docker compose up -d
```

Verify: `docker compose -f monitoring/docker-compose.yml config --quiet` must
exit 0 once the variables are set.

## 4. Metrics exposition & scraping

* Source of truth: `MetricsCollector.generate_prometheus_metrics()`
  (`src/trading/ops/metrics_collector.py`) exports
  `super_trademan_signals_total`, `super_trademan_fills_total`,
  `super_trademan_latency_p95_ms`, `super_trademan_latency_p99_ms`,
  `super_trademan_shadow_pnl_pct`, `super_trademan_max_drawdown_pct`,
  `super_trademan_staleness_trips`, `super_trademan_cpu_usage_pct`,
  `super_trademan_memory_usage_mb`.
* `/metrics` is protected by `MetricsAuthMiddleware`
  (`src/trading/observability/metrics_auth.py`): no configured token ⇒ every
  request denied (fail closed). Start the API with the same
  `METRICS_BEARER_TOKEN` you give compose.
* `prometheus/prometheus.yml` jobs:
  * `super_trademan-api` → `host.docker.internal:8080` over `/metrics`
    (compose adds the Linux `host-gateway` mapping; adjust the port if your
    API binds elsewhere).
  * `alertmanager` → `alertmanager:9093`; `loki` → `loki:3100`;
    `prometheus` self-scrape.
  * Rules loaded from `/etc/prometheus/alerts.yml` (= `prometheus/alerts.yml`),
    evaluated every 15s.
* Scrape health check: open http://127.0.0.1:9090/targets — the api job should
  be UP; a 401/403 there means the two tokens disagree.

### Alert rules contract

Severities follow the `AlertSeverity` vocabulary from
`src/trading/ops/alert_manager.py`: `warning | critical | emergency`. Every rule
carries a `runbook_url` into `docs/INCIDENT_RESPONSE.md`.

## 5. Alert routing (Alertmanager)

`alertmanager/alertmanager.yml` (committed with a literal
`__ALERT_WEBHOOK_URL__` placeholder; rendered at container start):

| Route | Match | Receiver | group_by | group_wait | group_interval | repeat_interval |
|---|---|---|---|---|---|---|
| root/default | everything else | warning-webhook | alertname, severity | 30s | 5m | 4h |
| emergency | `severity = "emergency"` | emergency-webhook | alertname | 0s | 5m | 30m |

Both receivers POST JSON (Alertmanager webhook v4 format, `send_resolved: true`)
to `$ALERT_WEBHOOK_URL`. The emergency branch honors EMERGENCY's
never-suppress semantics (no initial wait, half-hour re-notification).

Inspect live routing at http://127.0.0.1:9093 (status/config), or test end to
end by pushing a synthetic alert:

```bash
curl -sS -X POST http://127.0.0.1:9093/api/v2/alerts \
  -H 'Content-Type: application/json' \
  -d '[{"labels":{"alertname":"OBSERVABILITY_SELFTEST","severity":"emergency"},
        "annotations":{"summary":"OT-1 wiring self-test"}}]'
```

## 6. Logs (Loki) & distributed tracing (OpenTelemetry)

**Loki** runs single-binary with filesystem storage on the named volume
`loki_data`; retention is enforced at **7 days** (168h) by the compactor
(`retention_enabled: true` against the filesystem delete-request store).
Grafana ships a Loki data source away — add one pointing at
`http://loki:3100` (in-stack) or ship JSON logs in with Promtail/Alloy if you
want container stdout collected; wiring an agent is intentionally out of scope
here.

**Tracing** is opt-in and can never break imports:

```bash
pip install -e '.[otel]'          # optional-dependencies group in pyproject.toml
export OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4318/v1/traces"
```

With both in place, `setup_tracing("super_trademan-api")`
(`src/trading/observability/otel.py`, called from the API middleware) installs
a real TracerProvider + OTLP/HTTP exporter; otherwise spans are in-repo no-ops.
Every request gets a correlation id (reusing the inbound `X-Request-ID` header
when present, echoed back on the response) stored in the same ContextVar that
`ops.logging_config` already uses, so the JSON `trace_id` field carries either
the active W3C trace id (real tracing) or that correlation id.

## 7. Grafana dashboards

1. Log into Grafana at http://127.0.0.1:3000 (`admin` / `$GRAFANA_ADMIN_PASSWORD`).
2. Add data sources: **Prometheus** → `http://prometheus:9090`,
   **Loki** → `http://loki:3100`.
3. Import `monitoring/grafana/dashboards/super_trademan.json` if present,
   selecting the Prometheus data source.

## 8. Operational notes

* Image pins are 2023-era (audit F-028); upgrading is a separate change.
* All published ports are loopback-only; do not replace `127.0.0.1:` prefixes
  when editing compose.
* `.env` holding real credentials must never be committed (see `.gitignore`).
