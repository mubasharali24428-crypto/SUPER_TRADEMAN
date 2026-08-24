# Incident Response Manual — SUPER_TRADEMAN

This manual provides standard operating procedures (SOPs) for operational anomalies during system execution.

> Accuracy note (ALEX-FORCE SUB-08): claims below were checked against the
> current source where possible; anything that could not be verified in code is
> marked **TODO(verify)** and must be confirmed before you rely on it.

## 1. Exchange Downtime or API Timeout
- **Detection**: `VenueAdapter` (`src/trading/execution/venue_adapter.py`) returns timeouts or HTTP 5xx errors.
- **Immediate Action**: OMS keeps order intent in `SUBMITTED` or `ACKED` state; does not retry blindly.
- **Recovery**: Once exchange API recovers, run `reconcile_report.py` to synchronize state.

## 2. WebSocket Disconnect / Data Staleness
- **Detection**: `StalenessSentinel` (`src/trading/data/staleness.py`) detects latency $> 3000\text{ ms}$ or missing ticks.
- **Immediate Action**: `StalenessSentinel` trips circuit breaker; OMS rejects new entries with `STALE_DATA_REJECTION`.
- **Note**: `evaluate_exit_signal()` remains fully callable to protect open positions. TODO(verify): confirm the exit path bypasses the staleness gate under all order states.

## 3. Position or Balance Mismatch
- **Detection**: `StateReconciler` (`src/trading/execution/reconciler.py`) detects local position quantity != venue position quantity.
- **Immediate Action**: Reconciler flags state as `MISMATCH` / `QUARANTINE`. New order submissions are blocked.
- **Recovery**: Operator inspects venue positions, issues manual reconciliation via sanctioned `_ISSUER` exit flow, and verifies status returns to `CLEAN`.

## 4. Portfolio Drawdown Spike (Tier 3 Circuit Breaker)
- **Detection**: `PortfolioCircuitBreaker` (`src/trading/risk/portfolio_circuit_breaker.py`) observes aggregate portfolio drawdown $> 8\%$ (flatten threshold default `0.08`; an earlier entry-block tier triggers below it).
- **Immediate Action**: Circuit breaker emits `PORTFOLIO_FLATTEN` and dispatches `ApprovedExit` orders for all open positions using `_ISSUER`.
- **Fallback**: If flatten orders are unacknowledged after 10s, OMS logs `PORTFOLIO_FLATTEN_TIMEOUT_ALERT` and retries forced market exits until positions are verified closed. TODO(verify): confirm the 10-second timeout constant in the OMS source.

## 5. High Availability Failover & Audit Verification

### 5.1 Active-Passive Failover Procedure
1. `ActivePassiveManager` (`src/trading/infrastructure/ha_lock.py`, TTL default `ttl_sec = 15.0`) automatically maintains primary node locks with a 15-second TTL.
2. If the primary node stops emitting heartbeats for > 15 seconds, the secondary node automatically acquires the primary active lock and logs `HA_FAILOVER_TRIGGERED`.
3. To manually release an HA lock during node maintenance:
   ```bash
   python3 -c "from trading.infrastructure.ha_lock import ActivePassiveManager; ActivePassiveManager('node_1').release_lock()"
   ```

### 5.2 Automatic State Recovery on Failover
Upon taking over the primary lock, `StateRecoveryEngine` (`src/trading/infrastructure/state_recovery.py`):
1. Queries the venue for active open positions and orders.
2. Reconciles against local database state.
3. Automatically cancels orphaned local orders.
4. If severe position mismatches exist across multiple assets, issues a Tier 3 Flatten exit via sovereign `_ISSUER` token.

### 5.3 Audit Ledger Integrity Verification
Verify that the cryptographic SHA-256 audit ledger has not been tampered with (`AuditLedger.verify_chain_integrity()`, `src/trading/security/audit_ledger.py`):
```bash
python3 -c "from trading.security.audit_ledger import AuditLedger; print('Ledger Clean:', AuditLedger().verify_chain_integrity())"
```

## 6. Mode Demotion / Emergency Rollback During an Incident
When an incident requires dropping execution mode:
```bash
./scripts/rollback.sh   # fail-fast; exits NON-ZERO if any step fails
```
The script fires the portfolio circuit-breaker alert, runs preflight + kill-switch drills at `SHADOW`, then demotes via `scripts/promote_mode.py`. If it exits non-zero, the demotion did NOT complete cleanly — follow the printed manual steps (direct `promote_mode.py --to-mode SHADOW`, preflight re-check) before trusting the system is safe. See `docs/DEPLOYMENT_RUNBOOK.md` §3.5 for confirmation-token handling.

## 7. SOCRATIC GUARDRAIL ANSWERS (Included in Incident SOP)

1. *If Shadow Mode metrics look excellent but reconciliation reports show one unexplained mismatch, should the system promote to live trading?*  
   **No.** Absolute state consistency is non-negotiable. An unexplained mismatch in Shadow Mode will become a catastrophic capital leak under live trading. The root cause must be diagnosed and resolved first.

2. *If a kill-switch drill passes in Paper Mode but behaves differently in Shadow Mode against live market data, what does that reveal?*  
   It reveals market friction discrepancies (order book depth, exchange rate limits, or network latency). Shadow Mode accurately exposes live market realities that synthetic paper simulation misses.

3. *If the system generates five simultaneous signals but capital only supports two, what is the cost of rejecting the other three?*  
   The cost is opportunity loss on the 3 rejected signals. This cost is completely acceptable because over-allocating capital or reducing position sizes below the sovereign sizing formula compromises statistical edge and risks catastrophic over-exposure.

4. *If a human operator wants to bypass a failed preflight check because "we know it is fine," what architectural mechanism prevents this?*  
   `promote_mode.py` programmatically blocks mode promotion whenever preflight check or reconciliation fails. No CLI flag allows bypassing a blocking preflight failure.

5. *If the system cannot prove that it is safe, should it be allowed to trade?*  
   **No.** If certainty is missing, the system must not proceed. Capital follows proof.
