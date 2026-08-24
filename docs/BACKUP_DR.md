# Backup & Disaster Recovery (DR) Runbook

> **STATUS: TARGETS, NOT ACTUALS.** Every RTO/RPO number in this document is a
> **target** until the first real drill records actuals in the table below.
> `scripts/restore_drill.sh` prints the measured value on every run — paste it
> into the "Measured" column and date it. Until then, treat recovery-time
> claims as unverified engineering targets.

| Metric | Target | Recommended cadence | Measured (from drills) | Date |
|---|---|---|---|---|
| RPO (max tolerable data loss) | ≤ 14 days worst case; **24h with daily backups** | cron: daily `backup.sh` (see below) | _none yet — no real drill recorded_ | — |
| RTO (restore-to-verified) | **≤ 30 min** | weekly `drill.yml` CI run + ad-hoc | _none yet — no real drill recorded_ | — |

---

## 1. What exists where

| Artifact | Location | Produced by |
|---|---|---|
| Postgres dumps (custom format `-Fc`) | `<repo>/backups/<YYYYMMDD_HHMMSS>/trading-<TS>.dump` | `scripts/backup.sh` |
| State-file copies (`learning_graph.jsonl`, `.risk_tier_state.json`, `ops_alert_state.json`) | `<repo>/backups/<TS>/state/` | `scripts/backup.sh` |
| Integrity manifest | `<repo>/backups/<TS>/manifest.sha256` | `scripts/backup.sh` |
| Weekly restore verification | GitHub Actions `.github/workflows/drill.yml` | CI |

Retention: backup runs older than `RETENTION_DAYS` (default **14**) are pruned
by `backup.sh` each run. The RPO therefore equals the backup **cadence**, not
the retention window — run `backup.sh` at least daily for a 24h RPO.

Recommended crontab (host):

```
# daily 02:15 backup, 14-day retention
15 2 * * * cd <repo> && POSTGRES_PASSWORD="$(cat /secure/pg_pass)" ./scripts/backup.sh >>/var/log/backup.log 2>&1
```

## 2. Taking a backup

```bash
POSTGRES_PASSWORD=... ./scripts/backup.sh            # defaults: BACKUP_DIR=./backups RETENTION_DAYS=14
BACKUP_DIR=/vol/backups RETENTION_DAYS=30 POSTGRES_PASSWORD=... ./scripts/backup.sh
```

Behavior: refuses to run without `POSTGRES_PASSWORD`; streams `pg_dump -Fc`
from the running compose postgres service; copies any state files that exist;
writes `manifest.sha256`; sanity-checks the dump with `pg_restore --list`;
prunes expired runs. Idempotent — re-running is always safe.

## 3. Verifying a backup (drill)

```bash
POSTGRES_PASSWORD=... ./scripts/restore_drill.sh     # uses latest dump in BACKUP_DIR
```

The drill builds an isolated scratch compose project (`-p drill-<pid>`), starts
a fresh postgres:16 container, restores the dump, then asserts:

1. **Checksum** — restored file's sha256 matches `manifest.sha256`.
2. **Row counts** — `order_intent`, `decisions`, `deployment_metrics` must be
   non-zero. A zero count is reported as an explicit `WARN` (tolerated, never a
   silent pass); a hard failure exits non-zero.
3. **Measured RTO** — elapsed wall-clock from scratch-project start to last
   passing assertion is printed as `MEASURED RTO`.

The scratch project is torn down on success, failure, or interrupt (trap).
Set `DRILL_KEEP=1` to keep it for debugging.

## 4. Real disaster recovery — step by step

Restore order is strictly: **Postgres → state files → schema reconciliation
(alembic)**.

### Step 1 — Restore Postgres

```bash
LATEST=backups/$(ls backups | sort | tail -n1)
shasum -a 256 -c "$LATEST/manifest.sha256"          # verify integrity first
docker compose up -d postgres                        # fresh pgdata volume if needed:
# docker compose down -v && docker compose up -d postgres
docker compose exec -T postgres \
  pg_restore --no-owner --username "$POSTGRES_USER" --role "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" < "$LATEST"/trading-*.dump
```

### Step 2 — Restore state files

Copy the backed-up runtime state back to the repo root (they are plain
files; stop writers first):

```bash
cp "$LATEST/state/"* ./    # learning_graph.jsonl, .risk_tier_state.json, ops_alert_state.json
```

Any state file absent at backup time simply won't exist here either — services
recreate them at runtime.

### Step 3 — Schema reconciliation (alembic)

Schema lives in alembic migrations (`alembic/versions/`). After restoring a
dump taken from a migrated database, the schema already matches HEAD — do NOT
re-run migrations over it. Instead confirm/stamp:

```bash
POSTGRES_URL=postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:5432/$POSTGRES_DB \
  .venv/bin/alembic current        # should show the head revision
# If alembic reports an empty/unknown version table but tables exist (e.g. dump
# predates the alembic migration):
#   POSTGRES_URL=... .venv/bin/alembic stamp head
```

Only run `alembic upgrade head` on a database restored to a revision older
than current head.

### Step 4 — Validate & resume

Run `tests/test_db.py` against the restored DB, check service health endpoints,
then start the application stack (`docker compose up -d`).

## 5. Escalation

| Role | Contact | When |
|---|---|---|
| Primary on-call | `_PLACEHOLDER_` (fill in) | any failed backup or drill |
| Secondary / infra owner | `_PLACEHOLDER_` | primary unreachable > 15 min |
| Incident record | open issue + link in `docs/INCIDENT_RESPONSE.md` | every real DR event |

---
*Maintained by DR-1 squad. Update the measured-RTO table after every drill.*
