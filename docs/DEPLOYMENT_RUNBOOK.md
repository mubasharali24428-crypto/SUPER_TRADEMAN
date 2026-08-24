# Deployment Runbook — SUPER_TRADEMAN

## 1. System Overview
`SUPER_TRADEMAN` is an institutional-grade, backtest-verified algorithmic trading system for crypto perpetual futures. The execution hierarchy strictly progresses through:
`BACKTEST` -> `PAPER` -> `SHADOW` -> `LIVE_RESTRICTED` -> `LIVE_FULL`.

## 2. Environment Variables & Credentials
Ensure the following environment variables are securely loaded (never commit secrets to git):
- `EXECUTION_MODE`: `SHADOW` or `LIVE_RESTRICTED`
- `RISK_PCT`: `0.01` (hard capped $\le 0.02$)
- `EXCHANGE_API_KEY`: Exchange API key with withdrawal permissions **DISABLED**
- `EXCHANGE_API_SECRET`: Exchange API secret
- `POSTGRES_URL`: PostgreSQL connection string (read by `src/trading/config.py`; compose-level credentials are `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`)
- `PROMOTE_CONFIRMATION_TOKEN`: the expected human confirmation token for live-mode promotion — see §3.5

## 3. Operational CLI Commands

### 3.1 Preflight & Synthetic Multi-Agent Stress Test
Run synthetic multi-agent simulation & stress tests before Day 0:
```bash
python scripts/run_synthetic_campaign.py --duration 100 --chaos_mode mixed
python scripts/generate_synthetic_report.py
```

Run mandatory automated preflight check:
```bash
python scripts/preflight_check.py --mode SHADOW
```

### 3.2 Reconciliation Report
Run read-only reconciliation check:
```bash
python scripts/reconcile_report.py --format table
```

### 3.3 Shadow Mode Validation Report & Gate 1 Exit Codes
Generate Shadow Mode report and evaluate Gate 1:
```bash
python scripts/shadow_report.py --days 20 --format table
```

Exit codes (`shadow_report.py`):
| Code | Meaning |
|------|---------|
| `0`  | Gate PASS (all Gate-1 criteria met on sufficient data) |
| `1`  | Usage/runtime error (bad arguments, unexpected failure) |
| `2`  | INSUFFICIENT_DATA — fewer than 20 distinct persisted daily records; no gate decision is rendered |
| `3`  | GATE_FAIL — sufficient data, at least one criterion failed |

The store is fail-closed: the report never synthesizes or backfills metrics.

### 3.4 Operational Kill-Switch Drills
Execute deterministic safety drills:
```bash
python scripts/kill_switch_drill.py --mode SHADOW
```

### 3.5 Mode Promotion (confirmation token handling)
The promotion guard reads the **expected** confirmation token ONLY from the
`PROMOTE_CONFIRMATION_TOKEN` environment variable and compares it
(constant-time, `hmac.compare_digest`) to the value you supply via `--confirm`.
The value passed on the command line is the *candidate* token — it must match
the environment variable but is a different secret material from it. Supply
BOTH out-of-band (secret manager / CI masked variable); never inline real
token values in shell history, docs, or CI logs.

```bash
# In your shell/session secret loading (NOT in the command history):
export PROMOTE_CONFIRMATION_TOKEN='<expected-token-from-secret-store>'

# Then run (the --confirm VALUE comes from your out-of-band source):
python scripts/promote_mode.py --from-mode SHADOW --to-mode LIVE_RESTRICTED \
    --confirm '<matching-token-value>'
```

If the token is missing or does not match, promotion exits non-zero with
`[MODE_PROMOTION_BLOCKED]`. There is no CLI bypass for failed evidence gates.

## 4. Automated CI/CD Deployment & Rollback

### Automated Deployment Script (`scripts/deploy.sh`)
The script runs under `set -Eeuo pipefail` with an ERR trap: any failed step
aborts the deployment and prints a step summary. After promotion it runs a
health gate (`curl` retry loop against `$HEALTH_URL`, default
`http://127.0.0.1:8080/`; tune with `HEALTH_RETRIES` / `HEALTH_SLEEP_SECONDS`).

```bash
./scripts/deploy.sh --mode SHADOW
./scripts/deploy.sh --mode LIVE_RESTRICTED --confirm '<matching-token-value>'
# PROMOTE_CONFIRMATION_TOKEN must be set in the environment for live modes.
./scripts/deploy.sh --dry-run   # skips nothing except branch check + health gate
```

### Emergency Rollback Script (`scripts/rollback.sh`)
Also runs fail-fast (`set -Eeuo pipefail`). Every step is tracked; if any step
fails the script prints a failure summary and exits NON-ZERO — it will NOT
report success after a partial rollback.

```bash
./scripts/rollback.sh
```

## 5. Rollback Procedure
If any anomaly or circuit breaker trips during `LIVE_RESTRICTED`:
1. Execute emergency rollback `./scripts/rollback.sh`.
2. Demote mode back to `SHADOW` or `PAPER`.
3. Investigate root cause in logs and reconciliation reports before re-promoting.
4. If rollback itself fails (non-zero exit), follow the printed manual follow-up
   steps and page the on-call operator.
