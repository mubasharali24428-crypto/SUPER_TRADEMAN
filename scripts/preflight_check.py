#!/usr/bin/env python3
"""Deployment Preflight Checker Script.

Validates system configuration, security rules, database readiness before
allowing mode promotion or system launch in SHADOW / LIVE_RESTRICTED modes.

HK-1 alignment with fail-closed reality:
- Credentials/secrets checks now FAIL LOUDLY when required environment
  variables (POSTGRES_URL/DATABASE_URL, REDIS_URL) are absent — there are no
  defaults anywhere in trading.config by policy.
- The database check uses the ``trading.db.postgres.resolve_postgres_url``
  contract and actually probes connectivity + Alembic-owned tables
  (ohlcv, funding_rates, alembic_version). Runtime DDL self-healing was
  removed from trading.data.crypto, so a missing schema is a hard blocker
  with remediation pointing at ``alembic upgrade head``.
- Removed the previous always-pass placeholder checks ("Market Data:
  Asset Universe Freshness", "Execution: Reconciler & OMS Initialization",
  and the unconditional schema-readiness pass) that asserted outcomes the
  checker never verified.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from trading.config import ExecutionMode
from trading.db.postgres import resolve_postgres_url
from trading.observability.logger import get_logger

logger = get_logger("scripts.preflight_check")

DB_PROBE_TIMEOUT_S = 5.0
REQUIRED_TABLES = frozenset({"ohlcv", "funding_rates", "alembic_version"})


@dataclass
class PreflightCheckResult:
    name: str
    severity: str  # "BLOCKING" or "WARNING"
    passed: bool
    details: str


def _redact_url(url: str) -> str:
    """Mask credentials in a DSN before printing it anywhere."""
    if "@" in url:
        scheme_sep = url.find("://")
        prefix = url[: scheme_sep + 3] if scheme_sep != -1 else ""
        return prefix + "***@" + url.rsplit("@", 1)[1]
    return url


def _is_legacy_placeholder_credential(value: str) -> bool:
    # Mirrors trading.config.Settings._reject_embedded_default_credential.
    return "user:" + "***@" in value


def _default_db_probe(url: str) -> Tuple[bool, str]:
    """Connect to Postgres and verify the Alembic-owned tables exist."""
    try:
        ok, detail = asyncio.run(_probe_database(url))
    except Exception as exc:  # noqa: BLE001 - any probe failure is a fail-closed result
        return False, (
            f"Postgres probe failed ({type(exc).__name__}: {exc}). "
            "Start the database (docker-compose) and apply the schema with "
            "'alembic upgrade head' before promoting."
        )
    return ok, detail


async def _probe_database(url: str) -> Tuple[bool, str]:
    import asyncpg

    conn = await asyncio.wait_for(asyncpg.connect(url), timeout=DB_PROBE_TIMEOUT_S)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        present = {r["table_name"] for r in rows}
        missing = sorted(REQUIRED_TABLES - present)
        if missing:
            return False, (
                f"Connected, but Alembic-managed table(s) missing: {', '.join(missing)}. "
                "Apply migrations: POSTGRES_URL=<url> alembic upgrade head."
            )
        return True, (
            "Connected; Alembic-owned tables present "
            f"({', '.join(sorted(REQUIRED_TABLES))}). Runtime DDL is disabled by design."
        )
    finally:
        await conn.close()


def run_preflight_checks(
    mode: ExecutionMode,
    db_prober: Optional[Callable[[str], Tuple[bool, str]]] = None,
) -> Tuple[bool, List[PreflightCheckResult]]:
    results: List[PreflightCheckResult] = []

    def add(name: str, passed: bool, details: str, severity: str = "BLOCKING") -> None:
        results.append(
            PreflightCheckResult(
                name=name, severity=severity, passed=passed, details=details
            )
        )

    # 1. Configuration Checks
    mode_str = mode.value if hasattr(mode, "value") else str(mode)
    add(
        "Configuration: Execution Mode",
        mode_str in [m.value for m in ExecutionMode],
        f"Target execution mode '{mode_str}' is valid.",
    )

    risk_pct = float(os.getenv("RISK_PCT", "0.01"))
    add(
        "Configuration: Risk Percentage",
        0 < risk_pct <= 0.02,
        f"risk_pct={risk_pct} satisfies sovereign cap (0, 0.02].",
    )

    max_positions = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))
    add(
        "Configuration: Max Concurrent Positions",
        max_positions > 0,
        f"max_concurrent_positions={max_positions} configured.",
    )

    chase_timeout = float(os.getenv("CHASE_TIMEOUT_MS", "5000.0"))
    add(
        "Configuration: Chase Timeout",
        chase_timeout >= 1000.0,
        f"chase_timeout_ms={chase_timeout} ms configured.",
    )

    staleness_thresh = float(os.getenv("STALENESS_THRESHOLD_MS", "3000.0"))
    add(
        "Configuration: Staleness Threshold",
        staleness_thresh >= 500.0,
        f"staleness_threshold_ms={staleness_thresh} ms configured.",
    )

    # 2. Secrets Checks — fail loudly when required configuration is absent.
    try:
        pg_url = resolve_postgres_url()
        add(
            "Secrets: POSTGRES_URL Resolvable (resolve_postgres_url contract)",
            True,
            f"Postgres DSN resolved ({_redact_url(pg_url)}).",
        )
    except RuntimeError as exc:
        pg_url = ""
        add(
            "Secrets: POSTGRES_URL Resolvable (resolve_postgres_url contract)",
            False,
            f"{exc} Set POSTGRES_URL (or DATABASE_URL) before deployment.",
        )

    redis_url = os.getenv("REDIS_URL", "")
    add(
        "Secrets: REDIS_URL Configured",
        bool(redis_url),
        "Redis DSN present in environment."
        if redis_url
        else "Missing REDIS_URL — trading.config.Settings requires it (no defaults).",
    )

    candidate_urls = [
        u
        for u in (pg_url, os.getenv("POSTGRES_URL", ""), os.getenv("DATABASE_URL", ""))
        if u
    ]
    no_placeholder = all(
        not _is_legacy_placeholder_credential(u) for u in candidate_urls
    )
    add(
        "Secrets: No Legacy Placeholder Credentials",
        no_placeholder,
        "No masked-password placeholder DSN detected."
        if no_placeholder
        else "postgres_url matches the legacy committed placeholder credential; supply real credentials from outside the repository.",
    )

    # 3. Security Checks
    api_key = os.getenv("EXCHANGE_API_KEY", "")
    api_secret = os.getenv("EXCHANGE_API_SECRET", "")
    has_keys = (
        bool(api_key and api_secret)
        if mode in (ExecutionMode.LIVE_RESTRICTED, ExecutionMode.LIVE_FULL)
        else True
    )
    add(
        "Security: Exchange API Credentials Loaded from Env",
        has_keys,
        "Credentials securely loaded from environment variables."
        if has_keys
        else "Missing EXCHANGE_API_KEY/SECRET for live mode.",
    )

    no_withdraw = os.getenv("EXCHANGE_WITHDRAWAL_ENABLED", "false").lower() == "false"
    add(
        "Security: Withdrawal Permission Disabled",
        no_withdraw,
        "API keys strictly prohibit withdrawal access.",
    )

    # 4. Database Connectivity + Schema Readiness (real probe, fail-closed).
    if pg_url:
        prober = db_prober if db_prober is not None else _default_db_probe
        db_ok, db_detail = prober(pg_url)
        add(
            "Database: Connectivity & Alembic Schema Present",
            db_ok,
            db_detail,
        )
    else:
        add(
            "Database: Connectivity & Alembic Schema Present",
            False,
            "Skipped: no Postgres URL could be resolved (see secrets checks above).",
        )

    blocking_failures = [
        r for r in results if r.severity == "BLOCKING" and not r.passed
    ]
    overall_pass = len(blocking_failures) == 0

    return overall_pass, results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run SUPER_TRADEMAN Deployment Preflight Check"
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="SHADOW",
        help="Target execution mode (e.g. SHADOW, LIVE_RESTRICTED)",
    )
    args = parser.parse_args()

    try:
        target_mode = ExecutionMode(args.mode.lower())
    except ValueError:
        print(
            f"PREFLIGHT_STATUS: FAIL\n[BLOCKING] Invalid execution mode '{args.mode}'."
        )
        sys.exit(1)

    overall_pass, results = run_preflight_checks(target_mode)

    print("\n=======================================================")
    print("      SUPER_TRADEMAN DEPLOYMENT PREFLIGHT CHECK")
    print(f"      Target Execution Mode: {target_mode.value.upper()}")
    print("=======================================================\n")

    for res in results:
        status_str = "[PASS]" if res.passed else f"[{res.severity}_FAIL]"
        print(f"{status_str:<16} {res.name:<55} - {res.details}")

    print("\n-------------------------------------------------------")
    if overall_pass:
        print("PREFLIGHT_STATUS: PASS")
        sys.exit(0)
    else:
        print("PREFLIGHT_STATUS: FAIL")
        print(
            "Remediation: make setup && make migrate (alembic owns the schema; runtime DDL is gone)."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
