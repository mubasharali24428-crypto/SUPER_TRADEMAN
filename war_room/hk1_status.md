# HK-1 Status — Cleanup Debt: runtime-DDL removal, deps declaration, dead-code sweep

Repo: `/Users/user/AG PROJ1/algo-trading-system` @ main `9a62e7a` — **no commits made** (working-tree changes only).
Scope honored: only owned files touched (`src/trading/data/crypto.py`, `pyproject.toml` deps array, `scripts/preflight_check.py`, new `Makefile`, tests).

## Final: DONE — all verification gates green

| Gate | Result |
|---|---|
| `.venv/bin/python -c "import trading.data.crypto"` | OK; `ensure_schema` gone |
| `pytest tests/test_crypto_data.py tests/trading/data/ -q` | green (1 skipped = DB-gated integration, 21 passed incl. 5 new guard tests) |
| `pytest tests/ops/test_preflight_check.py -q` | green — 9 passed |
| `make -n test` / `test-fast` / `migrate` / `drill-backup` / `lint` dry-runs | all print correct recipes |
| Broader: `tests/ops/ tests/test_db.py`, `tests/api`, config/strategy suites | green |

## Changes

### 1. `src/trading/data/crypto.py`
- Removed `ensure_schema()` and its two call sites (`ingest_ohlcv`, `ingest_funding_rates`) — no more runtime `CREATE TABLE IF NOT EXISTS`.
- Added `SchemaMissingError(RuntimeError)` + `_translate_missing_table()`: maps `asyncpg.UndefinedTableError` (42P01) to a loud error whose message names the missing relation and says: *"the database schema is owned by Alembic … Apply migrations before ingesting: POSTGRES_URL=<url> alembic upgrade head"*.
- Pure data functions untouched: `fetch_ohlcv_with_backoff`, `fetch_ohlcv_range`, `store_ohlcv`, `store_funding_rates`. Module docstring documents schema ownership.

### 2. `pyproject.toml` `[project] dependencies` ONLY
```diff
     "scipy>=1.14",
     "networkx>=3.0",
+    # Schema is owned by Alembic ... versions matched to .venv:
+    "alembic>=1.13,<2",
+    "sqlalchemy[asyncio]>=2.0,<3",
+    "greenlet>=3",
 ]
```
Installed in .venv: alembic 1.19.1, sqlalchemy 2.0.52, greenlet 3.5.5 → all satisfy pins. No other pins bumped; api/otel groups untouched.

### 3. `scripts/preflight_check.py` (rewritten checks)
- **Secrets fail loudly**: POSTGRES_URL via the real `resolve_postgres_url()` contract (POSTGRES_URL→DATABASE_URL→RuntimeError), REDIS_URL presence, legacy masked-placeholder DSN rejection (mirrors `Settings._reject_embedded_default_credential`). DSNs redacted before printing.
- **DB check is real & fail-closed**: connects with a 5s timeout, verifies Alembic-owned tables (`ohlcv`, `funding_rates`, `alembic_version`) exist; missing tables → BLOCKING fail pointing at `alembic upgrade head`. Unreachable/skipped → BLOCKING fail.
- **Removed Phase-0-incompatible placeholders**: "Market Data: Asset Universe Freshness" and "Execution: Reconciler & OMS Initialization" always-pass rows deleted; unconditional schema-pass replaced by the probe.
- Kept: mode validity, RISK_PCT sovereign cap, MAX_CONCURRENT_POSITIONS, CHASE_TIMEOUT_MS, STALENESS_THRESHOLD_MS, live-mode exchange-key check, withdrawal-disabled check.
- `run_preflight_checks(mode, db_prober=None)` takes an injectable prober for tests.

### 4. `Makefile` (new)
Targets: `setup` (uv sync --locked || pip install -e .[api]), `test`, `test-fast` (-m 'not integration'), `coverage` (--cov, respects pyproject fail_under=60), `lint` (ruff if present else compileall sweep), `run-api` (.venv/bin/uvicorn trading.api.app:app :8000), `migrate` (+ guard requiring POSTGRES_URL/DATABASE_URL) / `migrate-down`, `drill-backup` (pg_dump custom-format into BACKUP_DIR) / `drill-restore` (pg_restore --clean --if-exists, BACKUP_FILE required), plus `help`.

### 5. Tests
- NEW `tests/trading/data/test_crypto_schema_guard.py`: UndefinedTableError(±relation_name) → SchemaMissingError with "alembic upgrade head" guidance; success passthrough; `ensure_schema` absence pinned; end-to-end `ingest_ohlcv` against fake pool raising UndefinedTableError.
- REWROTE `tests/ops/test_preflight_check.py`: pass-path w/ env+healthy DB (prober receives resolved DSN), missing POSTGRES_URL / REDIS_URL / placeholder DSN / unreachable DB / live-mode keys / risk cap each fail loudly; env isolated via monkeypatch.
- No test imported removed crypto ensure-schema functions (verified: only `tests/test_db.py` references `OutboxStore.ensure_schema` — different class, untouched).

## Notes for next squad
- Pre-existing failures (NOT HK-1): `tests/a11y/test_static_a11y.py` (8 fails) — untracked at HEAD, greps `web/` static assets only; unrelated to data/deps/preflight. Also pre-existing working-tree mods from other squads: `src/trading/api/app.py`, `src/trading/observability/otel.py`, `monitoring/docker-compose.yml`.
- `uv.lock` NOT regenerated (would exceed file ownership; `make setup` handles lock drift via pip fallback).
- Preflight CLI smoke with empty env exits FAIL listing missing secrets + DB skip with remediation line — intended fail-closed behavior.
