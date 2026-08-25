# Performance Budgets — SUPER_TRADEMAN

> **VC-010 / VC-011 / VC-013 remediation.** Every number in this document is a
> *measured* value from the runs described below, not an estimate. Re-measure
> after hardware or dependency changes; budgets are meaningless without their
> measurement context.

- Machine: Apple-silicon Mac, local dev host (single-node numbers).
- Date: 2026-08-25 (W6 audit rectification pass).
- Environment: repo `.venv` (Python 3.12), Redis 8.8.0, Postgres 16 (native
  `initdb`/`postgres`, loopback), alembic 1.19.1 + SQLAlchemy 2.0.52.

## 1. OTel span overhead per request (VC-010)

Method: micro-benchmark of `Tracer.start_as_current_span("op")` around a no-op
body, 20,000 spans × 5 reps, best-of reported:

- no-op tracer (tracing disabled — what production uses when the `[otel]`
  extra is not installed): **3.83 µs/span**.
- real SDK `TracerProvider` with **no exporter** attached: **11.22 µs/span**
  (pure span create/end cost; exporter batching is separate).
- **Measured overhead: 7.39 µs/span = 0.0148% of a 50 ms per-cycle latency
  budget.**

Interpretation against the 60 s heartbeat cycle (one request-shaped unit ≈ one
span):

| Configuration | Cost per span | % of 50 ms per-cycle latency budget |
|---|---|---|
| No-op tracer | 3.83 µs | 0.008% |
| SDK, no exporter | 11.22 µs | 0.022% |
| SDK overhead vs no-op | **+7.39 µs** | **0.015%** |

**Budget ruling:** enabling real tracing costs ~2 additional milliseconds of
CPU per *thousand* requests — far under 0.1% of the latency budget even with
the full SDK active. The dominant risk is NOT CPU overhead but exporter
misconfiguration (VA-013's exfil note); keep `OTEL_EXPORTER_OTLP_ENDPOINT`
unset unless a collector is intended.

*Exact run:* `/tmp/bravo_otel.log` (`NOOP_US=3.8339`, `SDK_US=11.2227`,
`OVERHEAD_US=7.3888`); reproduce with the same 20k-span best-of-5 loop around
`start_as_current_span`.

## 2. Redis RTT & heartbeat-cycle budget (VC-011)

Method: raw RESP `PING` over a fresh loopback socket, 1000 iterations,
percentiles of the full connect+ping+teardown round trip (deliberately
conservative — the daemon pools connections, so steady-state RTT is lower):

| Stat | Value |
|---|---|
| min | 0.0566 ms |
| p50 | 0.1064 ms |
| p95 | 0.1737 ms |
| mean | 0.1144 ms |

Heartbeat-cycle accounting: the survival tier state does one read
(`load_state`) and one write (`save_state`) per cycle ⇒ **2 RTTs/cycle**:

| Path | Budget used |
|---|---|
| Tier load+save (2×p95) | **0.35 ms** |
| Worst-case 2×mean | 0.23 ms |

**Budget ruling:** even with unfriendly per-call sockets, tier-state I/O
consumes <1% of a 60 s heartbeat interval. A remote Redis at 10 ms RTT would
use 20 ms/cycle (~0.03%) — still negligible; alert only when RTT approaches
the heartbeat interval itself.

## 3. Alembic cold-start / migration window (VC-013)

Method: throwaway native Postgres 16 cluster (`initdb -U trader`,
trust-auth, port 55432), empty `trader` DB, timed
`.venv/bin/alembic upgrade head` subprocess runs against the repo checkout:

| Scenario | Measured |
|---|---|
| Cold start: empty DB → head (full initial migration) | **1.82 s** |
| Idempotent re-run (already at head) | 0.42 s |
| `alembic current` (CLI/config load floor) | 0.42 s |

**Budget ruling:** schema bring-up is ~2 s wall clock; the CLI/config floor is
~0.4 s. Any future migration that pushes `upgrade head` beyond a double-digit
second budget deserves its own drill entry here before deploy.

## 4. Test-suite coverage baseline (VC-030 / VC-033)

Measured with the now-declared `pytest-cov>=5` dev dependency (installed:
pytest-cov 7.1.0 / coverage 7.15.4) via `make coverage`'s exact invocation
(`pytest -q --cov=src --cov-report=term-missing`) on the pre-change tree:

| Metric | Baseline |
|---|---|
| **Total branch coverage** | **87.45%** |
| Statements covered / total | 5848 / 6505 (657 missed) |
| Branch totals | 1231 / 1480 covered (249 partial) |
| Suite result | 742 passed, 7 skipped, 0 failed |
| Gate | `fail_under = 60` — **passes with real margin** |

**Budget ruling:** the 60% floor was previously unenforceable fiction
(pytest-cov absent from `.venv`). It is now both declared and demonstrably
clearable at 87%+; treat any PR that moves the number below ~85% as a flag
for review even though the hard gate stays at 60%.

---

### Maintenance contract

Re-run these three measurements (span micro-bench, RESP PING percentiles,
timed `upgrade head` on a scratch cluster) whenever: the OTel SDK major
version changes, Redis client/version changes, or a new Alembic revision
lands. Paste updated numbers in place; do not let this file become another
estimate sheet.
