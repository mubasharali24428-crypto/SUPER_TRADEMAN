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
- `DATABASE_URL`: PostgreSQL connection string

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

### 3.3 Shadow Mode Validation Report
Generate Shadow Mode report and evaluate Gate 1:
```bash
python scripts/shadow_report.py --days 20 --format table
```

### 3.4 Operational Kill-Switch Drills
Execute deterministic safety drills:
```bash
python scripts/kill_switch_drill.py --mode SHADOW
```

### 3.5 Mode Promotion
Promote execution mode with required confirmation token:
```bash
python scripts/promote_mode.py --from-mode SHADOW --to-mode LIVE_RESTRICTED --confirm I_UNDERSTAND_THE_RISK
```

## 4. Automated CI/CD Deployment & Rollback

### Automated Deployment Script:
```bash
./scripts/deploy.sh --mode SHADOW
./scripts/deploy.sh --mode LIVE_RESTRICTED --confirm I_UNDERSTAND_THE_RISK
```

### Emergency Rollback Script:
```bash
./scripts/rollback.sh
```

## 5. Rollback Procedure
If any anomaly or circuit breaker trips during `LIVE_RESTRICTED`:
1. Execute emergency rollback `./scripts/rollback.sh`.
2. Demote mode back to `SHADOW` or `PAPER`.
3. Investigate root cause in logs and reconciliation reports before re-promoting.

