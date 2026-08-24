#!/usr/bin/env bash
# =============================================================================
# backup.sh — Postgres + state-file backups for the algo trading system.
#
# Produces, under BACKUP_DIR (default ./backups):
#   <TS>/trading-<TS>.dump          pg_dump custom format (-Fc)
#   <TS>/state/                     copies of runtime state files
#   <TS>/manifest.sha256            sha256 manifest of everything in this run
#
# Retention: dumps older than RETENTION_DAYS (default 14) are pruned each run.
# Idempotent: safe to re-run; each run writes a fresh timestamped directory and
# never mutates previous backups. Exit 0 only when every step succeeded.
#
# Usage:
#   POSTGRES_PASSWORD=... ./scripts/backup.sh
#
# Environment:
#   POSTGRES_USER     db owner            (default: trading)
#   POSTGRES_DB       database name       (default: trading)
#   POSTGRES_PASSWORD required — refuses to run when unset/empty
#   COMPOSE_FILE      optional explicit compose file
#   BACKUP_DIR        destination root    (default: ./backups)
#   RETENTION_DAYS    prune window        (default: 14)
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

POSTGRES_USER="${POSTGRES_USER:-trading}"
POSTGRES_DB="${POSTGRES_DB:-trading}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

log()  { printf '[backup] %s\n' "$*"; }
warn() { printf '[backup] WARN: %s\n' "$*" >&2; }
die()  { printf '[backup] ERROR: %s\n' "$*" >&2; exit 1; }

trap 'rc=$?; if (( rc != 0 )); then log "failed at line $LINENO (exit $rc); partial output left in place for inspection"; fi' EXIT

# --- Preconditions -----------------------------------------------------------
[[ -n "${POSTGRES_PASSWORD:-}" ]] || die "POSTGRES_PASSWORD is not set -- refusing to run (no default creds by policy)."
command -v docker >/dev/null 2>&1 || die "docker not found on PATH."
docker compose version >/dev/null 2>&1 || die "'docker compose' plugin not available."

# Satisfy compose's ${VAR:?} interpolation for services we do NOT touch here
# (e.g. REDIS_PASSWORD) without inventing or weakening real credentials.
export REDIS_PASSWORD="${REDIS_PASSWORD:-dummy-not-a-real-secret}"

TS="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$BACKUP_DIR/$TS"
mkdir -p "$RUN_DIR/state"

# --- Locate the postgres container -------------------------------------------
PG_CID="$(docker compose ${COMPOSE_FILE:+-f "$COMPOSE_FILE"} ps -q postgres | head -n1)"
[[ -n "$PG_CID" ]] || die "No running 'postgres' service found (docker compose ps). Start the stack first."
PG_STATE="$(docker inspect -f '{{if .State.Running}}running{{else}}{{.State.Status}}{{end}}/{{.State.Health.Status}}' "$PG_CID")"
case "$PG_STATE" in
  running/healthy) log "postgres service healthy." ;;
  running/*)       warn "postgres running but health=${PG_STATE#running/}; continuing." ;;
  *)               die "postgres service is not running (state=$PG_STATE)." ;;
esac

# --- Dump -------------------------------------------------------------------
DUMP_FILE="$RUN_DIR/trading-$TS.dump"
log "pg_dump (custom format) -> $DUMP_FILE"
if ! docker compose exec -T postgres \
      pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc >"$DUMP_FILE"; then
  rm -f "$DUMP_FILE"
  die "pg_dump failed."
fi

# --- State files -------------------------------------------------------------
STATE_FILES=(learning_graph.jsonl .risk_tier_state.json ops_alert_state.json)
for f in "${STATE_FILES[@]}"; do
  if [[ -f "$REPO_ROOT/$f" ]]; then
    cp "$REPO_ROOT/$f" "$RUN_DIR/state/"
    log "copied state file: $f"
  else
    warn "state file absent (skipped): $f"
  fi
done

# --- Manifest ----------------------------------------------------------------
( cd "$RUN_DIR" && find . -type f ! -name 'manifest.sha256' -print0 \
    | sort -z | xargs -0 sha256sum > manifest.sha256 )
SHA_LINE_COUNT="$(wc -l <"$RUN_DIR/manifest.sha256" | tr -d ' ')"
[[ "$SHA_LINE_COUNT" -ge 1 ]] || { rm -f "$RUN_DIR/manifest.sha256"; die "manifest came back empty."; }
log "manifest written ($SHA_LINE_COUNT entries): $(head -n1 "$RUN_DIR/manifest.sha256" | awk '{print substr($1,1,12)"...  "$2}')"

# Sanity-check the dump via stdin so no dump bytes are copied into any container.
docker compose exec -T postgres sh -c "pg_restore --list" <"$DUMP_FILE" >/dev/null \
  && log "dump readable (pg_restore --list OK)" \
  || die "dump unreadable by pg_restore"

# --- Prune -------------------------------------------------------------------
PRUNED=0
if [[ -d "$BACKUP_DIR" ]]; then
  while IFS= read -r -d '' old; do
    rm -rf "$old"
    PRUNED=$((PRUNED + 1))
    log "pruned expired backup: $(basename "$old")"
  done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d ! -path "$RUN_DIR" \
             -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]' \
             -mtime +"$RETENTION_DAYS" -print0)
fi
log "prune done ($PRUNED removed; retention RETENTION_DAYS=$RETENTION_DAYS)."
log "DONE: backup complete -> $RUN_DIR"
