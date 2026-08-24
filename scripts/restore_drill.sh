#!/usr/bin/env bash
# =============================================================================
# restore_drill.sh — Prove the backups actually restore.
#
# Spins up an ISOLATED scratch compose project (temp dir, project name
# drill-<pid>), restores the LATEST dump with pg_restore, asserts the restored
# database is real (row counts, checksums), measures elapsed wall-clock time as
# the MEASURED RTO, and always tears the scratch project down.
#
# Exit codes: 0 = drill passed; non-zero on any assertion failure.
#
# Usage:
#   POSTGRES_PASSWORD=... ./scripts/restore_drill.sh
#
# Environment:
#   POSTGRES_USER     db owner used in dumps        (default: trading)
#   POSTGRES_DB       database name                 (default: trading)
#   POSTGRES_PASSWORD required — refuses to run when unset/empty
#   BACKUP_DIR        where backup.sh writes runs   (default: ./backups)
#   DRILL_KEEP        set to 1 to keep the scratch project for debugging
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

POSTGRES_USER="${POSTGRES_USER:-trading}"
POSTGRES_DB="${POSTGRES_DB:-trading}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"

log()  { printf '[drill] %s\n' "$*"; }
warn() { printf '[drill] WARN: %s\n' "$*"; }
die()  { printf '[drill] ERROR: %s\n' "$*" >&2; exit 1; }

# --- Preconditions -----------------------------------------------------------
[[ -n "${POSTGRES_PASSWORD:-}" ]] || die "POSTGRES_PASSWORD is not set -- refusing to run."
command -v docker >/dev/null 2>&1 || die "docker not found on PATH."

LATEST_RUN="$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
                -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]' \
                | sort | tail -n1)"
[[ -n "$LATEST_RUN" ]] || die "no timestamped backup runs found in $BACKUP_DIR -- run scripts/backup.sh first."
DUMP_FILE="$(find "$LATEST_RUN" -maxdepth 1 -type f -name '*.dump' | sort | tail -n1)"
[[ -n "$DUMP_FILE" ]] || die "no .dump file in $LATEST_RUN."
MANIFEST="$LATEST_RUN/manifest.sha256"
log "latest dump: $DUMP_FILE"

# --- Scratch project scaffolding --------------------------------------------
SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/drill.XXXXXX")"
PROJECT="drill-$$"
COMPOSE_FILE="$SCRATCH_DIR/docker-compose.yml"
cat >"$COMPOSE_FILE" <<EOF
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: $POSTGRES_USER
      POSTGRES_PASSWORD: "$POSTGRES_PASSWORD"
      POSTGRES_DB: $POSTGRES_DB
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
      interval: 2s
      timeout: 3s
      retries: 30
      start_period: 5s
volumes: {}
EOF

DRILL_STARTED="$(date +%s)"
cleanup() {
  # Preserve the script's real exit status: without this, the trap's own
  # success would mask assertion failures (a drill can never false-pass).
  local rc=$?
  trap - EXIT
  if [[ "${DRILL_KEEP:-0}" != "1" && -n "$(docker compose -p "$PROJECT" ps -q 2>/dev/null)" ]]; then
    docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
    log "scratch project '$PROJECT' torn down (rc=$rc path)."
  fi
  rm -rf "$SCRATCH_DIR"
  exit "$rc"
}
trap cleanup EXIT

pexec() { docker compose -p "$PROJECT" exec -T postgres "$@"; }

# --- Bring up fresh postgres -------------------------------------------------
log "starting scratch postgres (project=$PROJECT)..."
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --quiet-pull
for _ in $(seq 1 60); do
  st="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose -p "$PROJECT" ps -q postgres)")" || st=unknown
  [[ "$st" == "healthy" ]] && break
  sleep 1
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose -p "$PROJECT" ps -q postgres)" 2>/dev/null)" == "healthy" ]] \
  || die "scratch postgres never became healthy."

# --- Restore + assert --------------------------------------------------------
log "restoring dump via pg_restore..."
pexec pg_restore --no-owner \
  --username="$POSTGRES_USER" --role="$POSTGRES_USER" \
  --dbname="$POSTGRES_DB" <"$DUMP_FILE"

# Checksum: the bytes we restored must hash to what the manifest recorded.
# Manifest entries are stored as ./<name>; strip the ./ prefix when matching.
if [[ -f "$MANIFEST" ]]; then
  EXPECTED_SHA="$(awk -v f="$(basename "$DUMP_FILE")" \
                    '{p=$NF; sub(/^\.\//,"",p); if (p==f) print $1}' "$MANIFEST" | tail -n1)"
  ACTUAL_SHA="$(shasum -a 256 "$DUMP_FILE" | awk '{print $1}')"
  if [[ -z "$EXPECTED_SHA" ]]; then
    warn "manifest has no entry for $(basename "$DUMP_FILE") -- checksum assertion SKIPPED."
  elif [[ "$EXPECTED_SHA" == "$ACTUAL_SHA" ]]; then
    log "checksum OK (${ACTUAL_SHA:0:12}...)"
  else
    die "checksum MISMATCH: manifest=$EXPECTED_SHA actual=$ACTUAL_SHA"
  fi
else
  warn "no manifest.sha256 in $LATEST_RUN -- checksum assertion SKIPPED."
fi

FAILURES=0
CHECKS_RUN=0
assert_counts() {
  # args: table min_expected label
  local tbl="$1" min="${2:-0}" label
  label="${3:-$tbl}"
  CHECKS_RUN=$((CHECKS_RUN + 1))
  local out count
  out="$(pexec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
          "SELECT count(*) FROM $tbl;")"
  if [[ ! "$out" =~ ^[0-9]+$ ]]; then
    die "row-count query failed for $tbl (got: ${out:-<empty>})"
  fi
  count="$out"
  if (( count > 0 )); then
    log "table $label: $count rows -- OK"
  elif (( min > 0 )); then
    printf '[drill] ERROR: %s: expected >%d rows, got 0\n' "$label" "$((min - 1))" >&2
    FAILURES=$((FAILURES + 1))
  else
    printf '[drill] WARN: %s has 0 rows (tolerated, but investigate before trusting this table)\n' "$label" >&2
  fi
}

assert_counts order_intent 1
assert_counts decisions 1
assert_counts deployment_metrics 1

# Completeness gate: the three assertions above MUST all have executed. A
# silent early-exit would otherwise let an empty restore "pass".
(( CHECKS_RUN == 3 )) || die "assertion completeness gate tripped: only $CHECKS_RUN/3 row-count checks ran."
(( FAILURES == 0 )) || die "$FAILURES row-count assertion(s) failed."
log "all assertions passed."

RTO_SECONDS=$(( $(date +%s) - DRILL_STARTED ))
printf '[drill] MEASURED RTO (restore-to-verified): %ds (%dm%02ds)\n' \
  "$RTO_SECONDS" "$((RTO_SECONDS / 60))" "$((RTO_SECONDS % 60))"
log "PASS: restore drill complete."
