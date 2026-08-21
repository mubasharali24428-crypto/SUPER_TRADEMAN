# Day 0 Live Deployment Checklist — SUPER_TRADEMAN

Must complete and verify all items before enabling `LIVE_RESTRICTED` mode:

- [ ] GitHub Actions CI pipeline `deployment_validation.yml` passes on `main`.
- [ ] `./scripts/deploy.sh --dry-run` executes without errors.
- [ ] Preflight check output is `PREFLIGHT_STATUS: PASS`.
- [ ] Reconciliation report output is `RECONCILIATION STATUS: CLEAN`.
- [ ] Kill-switch drill harness output is `DRILL HARNESS RESULT: PASS` across all 7 drills.
- [ ] Shadow Mode Gate 1 output is `GATE 1 STATUS: PASS`.
- [ ] Prometheus & Grafana stack active (`monitoring/docker-compose.yml`).
- [ ] AlertManager Slack / PagerDuty webhook destinations verified.
- [ ] Account capital is micro-sized for initial live restricted launch.
- [ ] `risk_pct` is set to `0.001` or lower.
- [ ] `max_concurrent_positions` is set to `2`.
- [ ] `max_portfolio_exposure_pct` is set to `0.10` or lower.
- [ ] Exchange API keys strictly have **NO withdrawal permissions**.
- [ ] IP allowlist is configured on exchange API key settings.
- [ ] Human operator is present for live monitoring.
- [ ] Emergency rollback script `./scripts/rollback.sh` tested and operational.
