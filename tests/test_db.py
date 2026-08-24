"""DB layer tests: real connectivity (skips w/o env), migration-owned schema contract.

Real-Postgres tests skip cleanly when POSTGRES_URL / DATABASE_URL is not set or
the server is unreachable. Schema itself comes from Alembic migrations
(alembic/versions/0001_initial) -- runtime code must never CREATE TABLE.
"""

import os

import pytest
import asyncpg

from trading.config import Settings
from trading.db.postgres import get_pool, resolve_postgres_url

pytestmark = [
    pytest.mark.skipif(
        not (os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")),
        reason="POSTGRES_URL/DATABASE_URL not set -- skipping live-DB tests",
    )
]


class FakePool:
    """Minimal asyncpg.Pool stand-in recording executed statements."""

    def __init__(self):
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append(("execute", sql, args))
        return "OK"

    async def executemany(self, sql, rows):
        self.executed.append(("executemany", sql, list(rows)))
        return "OK"

    async def fetch(self, sql, *args):
        return []


async def test_postgres_connects():
    settings = Settings()
    try:
        pool = await get_pool(settings)
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"PostgreSQL not reachable at {settings.postgres_url}: {e}")
    try:
        assert await pool.fetchval("SELECT 1") == 1
    finally:
        await pool.close()


async def test_redis_connects():
    from trading.db.redis import get_redis

    settings = Settings()
    client = get_redis(settings)
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Idempotency contracts against fakes (no Postgres required to validate SQL shape)
# ---------------------------------------------------------------------------


async def test_journal_store_decisions_is_idempotent_on_decision_id():
    from datetime import datetime, timezone
    from trading.journal import DecisionRecord, decision_id_for, store_decisions

    pool = FakePool()
    rec = DecisionRecord(
        asset="BTC/USDT",
        entry_time=datetime(2026, 8, 24, tzinfo=timezone.utc),
        side="long",
        decision="breakout continuation",
        expected_reward_risk=2.0,
        consequence_r_multiple=1.5,
        consequence_net_pnl=150.0,
        exit_reason="target",
        verdict="thesis_confirmed",
    )

    await store_decisions(pool, [rec, rec])

    kind, sql, rows = pool.executed[0]
    assert kind == "executemany"
    assert "ON CONFLICT (decision_id) DO UPDATE" in sql
    # Deterministic identity: identical records share one key -> one row on conflict.
    assert rows[0][0] == rows[1][0] == decision_id_for(rec)
    assert len(rows) == 2


async def test_outbox_has_no_runtime_ensure_schema():
    from trading.execution.outbox import OutboxStore

    assert not hasattr(OutboxStore, "ensure_schema"), (
        "schema ownership moved to alembic/versions/0001_initial -- "
        "runtime DDL must stay removed"
    )


async def test_outbox_save_intent_uses_conflict_do_nothing():
    from datetime import datetime, timezone
    from trading.execution.outbox import OrderIntent, OutboxStore
    from trading.execution.state_machine import OrderState

    pool = FakePool()
    store = OutboxStore(pool)
    intent = OrderIntent(
        client_order_id="s:sig:id",
        strategy_id="s",
        signal_id="sig",
        asset="BTC/USDT",
        side="buy",
        price=100.0,
        stop_price=95.0,
        quantity=0.1,
        created_at=datetime.now(timezone.utc),
        status=OrderState.CREATED,
    )
    await store.save_intent(intent)

    _, sql, _ = pool.executed[0]
    assert "ON CONFLICT (client_order_id) DO NOTHING" in sql


def test_resolve_postgres_url_requires_env_when_no_settings(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        resolve_postgres_url()
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pw@localhost:5432/trading")
    assert resolve_postgres_url().endswith(":5432/trading")
