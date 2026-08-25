# SUPER_TRADEMAN developer entrypoint (HK-1).
#
# Conventions:
# - Python/tooling always goes through .venv/bin so targets work regardless of
#   which environment is active in the calling shell.
# - `setup` prefers the locked uv environment and falls back to a plain
#   editable install with the optional [api] group.
# - Schema is owned by Alembic (alembic/versions/): runtime DDL self-healing
#   was removed from trading.data.crypto, so `make migrate` is part of setup.

VENV := .venv/bin
PYTHON := $(VENV)/python

.DEFAULT_GOAL := help
.PHONY: help setup test test-fast coverage lint run-api migrate migrate-down drill-backup drill-restore docker-up docker-down docker-logs

help:
	@echo "Targets:"
	@echo "  make setup        Install/sync deps (uv sync --locked, fallback pip install -e .[api])"
	@echo "  make test         Full test suite (pytest -q)"
	@echo "  make test-fast    Skip integration-marked tests (pytest -m 'not integration' -q)"
	@echo "  make coverage     Test suite with branch coverage report (fail under 60%)"
	@echo "  make lint         Ruff over src/ tests/ scripts/ (noqa-respected, exit on error)"
	@echo "  make run-api      Serve trading.api.app:app on 0.0.0.0:8000"
	@echo "  make migrate      Apply schema: alembic upgrade head (requires POSTGRES_URL)"
	@echo "  make migrate-down Roll back one migration (alembic downgrade -1)"
	@echo "  make drill-backup Postgres logical backup passthrough (pg_dump; BACKUP_DIR, default ./backups)"
	@echo "  make drill-restore Restore latest backup passthrough (pg_restore/psql; BACKUP_FILE required)"
	@echo "  make docker-up     Start infrastructure services (postgres+redis; needs .env creds)"
	@echo "  make docker-down   Stop infrastructure services (volumes preserved)"
	@echo "  make docker-logs   Tail infrastructure service logs"

setup:
	@if command -v uv >/dev/null 2>&1 && uv sync --locked; then \
		echo "[setup] uv sync --locked OK"; \
	else \
		echo "[setup] uv unavailable or lock out of date; falling back to pip"; \
		$(PYTHON) -m pip install -e ".[api]"; \
	fi

test:
	$(PYTHON) -m pytest -q

test-fast:
	$(PYTHON) -m pytest -m 'not integration' -q

coverage:
	$(PYTHON) -m pytest -q --cov=src --cov-report=term-missing --cov-report=xml

lint:
	@if [ -x $(VENV)/ruff ]; then \
		$(VENV)/ruff check src tests scripts; \
	else \
		echo "[lint] ruff not installed in .venv — running compileall syntax sweep instead"; \
		$(PYTHON) -m compileall -q src tests scripts; \
	fi

run-api:
	$(VENV)/uvicorn trading.api.app:app --host 0.0.0.0 --port 8000

migrate:
	@test -n "$$POSTGRES_URL$$DATABASE_URL" || { \
		echo "[migrate] POSTGRES_URL (or DATABASE_URL) must be set — no credential defaults by policy."; \
		exit 1; \
	}
	$(VENV)/alembic upgrade head

migrate-down:
	@test -n "$$POSTGRES_URL$$DATABASE_URL" || { \
		echo "[migrate-down] POSTGRES_URL (or DATABASE_URL) must be set."; \
		exit 1; \
	}
	$(VENV)/alembic downgrade -1

drill-backup:
	@test -n "$$POSTGRES_URL" || { echo "[drill-backup] POSTGRES_URL must be set."; exit 1; }
	@mkdir -p "$(or $(BACKUP_DIR),./backups)"
	pg_dump --format=custom --file="$(or $(BACKUP_DIR),./backups)/trading_$$(date +%Y%m%d_%H%M%S).dump" "$$POSTGRES_URL"

drill-restore:
	@test -n "$(BACKUP_FILE)" || { echo "[drill-restore] BACKUP_FILE=<path> required."; exit 1; }
	@test -n "$$POSTGRES_URL" || { echo "[drill-restore] POSTGRES_URL must be set."; exit 1; }
	pg_restore --clean --if-exists --dbname="$$POSTGRES_URL" "$(BACKUP_FILE)"

# VC-023: compose convenience passthroughs for the infrastructure stack
# (root docker-compose.yml: postgres + redis, loopback-only ports).
docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=200
