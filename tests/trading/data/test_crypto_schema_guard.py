"""HK-1: trading.data.crypto must not self-heal the schema at runtime.

The runtime ``CREATE TABLE IF NOT EXISTS`` DDL (former ``ensure_schema``) was
removed — Alembic owns the schema (alembic/versions/). These tests pin the
fail-closed contract:

- ``ensure_schema`` is gone.
- Storing into a missing table surfaces ``SchemaMissingError`` with guidance
  pointing at ``alembic upgrade head``, instead of silently creating tables.
"""

import asyncpg
import pytest

import trading.data.crypto as crypto_mod
from trading.data.crypto import (SchemaMissingError, _translate_missing_table,
                                 ingest_ohlcv)


class _NamedUndefinedTable(asyncpg.UndefinedTableError):
    """UndefinedTableError carrying asyncpg's optional ``relation_name``."""

    def __init__(self, relation_name: str):
        super().__init__(f'relation "{relation_name}" does not exist')
        self.relation_name = relation_name


@pytest.mark.parametrize("with_relation_name", [True, False])
async def test_translate_missing_table_maps_undefined_table(with_relation_name):
    if with_relation_name:
        exc: asyncpg.UndefinedTableError = _NamedUndefinedTable("ohlcv")
    else:

        class _Bare(asyncpg.UndefinedTableError):
            pass

        exc = _Bare('relation "funding_rates" does not exist')

    async def _boom():
        raise exc

    with pytest.raises(SchemaMissingError) as err:
        await _translate_missing_table(_boom())
    assert "alembic upgrade head" in str(err.value)
    if with_relation_name:
        assert "ohlcv" in str(err.value)
    assert isinstance(err.value.__cause__, asyncpg.UndefinedTableError)


async def test_translate_missing_table_passes_success_through():
    async def _ok():
        return 42

    assert await _translate_missing_table(_ok()) == 42


def test_ensure_schema_removed():
    """Runtime DDL entrypoint must be gone from trading.data.crypto."""
    assert not hasattr(crypto_mod, "ensure_schema"), (
        "trading.data.crypto.ensure_schema was removed (Alembic owns the "
        "schema); do not reintroduce runtime CREATE TABLE self-healing."
    )


class _FakePool:
    """Duck-typed asyncpg.Pool whose executemany hits a missing table."""

    async def executemany(self, *args, **kwargs):
        raise asyncpg.UndefinedTableError('relation "ohlcv" does not exist')


class _FakeExchange:
    id = "binance"

    def parse_timeframe(self, timeframe):  # pragma: no cover - unused here
        return 3600


async def test_ingest_ohlcv_raises_schema_missing_on_missing_tables(monkeypatch):
    """End-to-end ingest path maps UndefinedTableError -> SchemaMissingError."""

    async def _fake_fetch(exchange, symbol, timeframe, since, limit, max_retries=5):
        return [
            [1_578_300_000_000 + i * 3_600_000, 1.0, 2.0, 0.5, 1.5, 10.0]
            for i in range(3)
        ]

    monkeypatch.setattr(crypto_mod, "fetch_ohlcv_with_backoff", _fake_fetch)

    with pytest.raises(SchemaMissingError) as err:
        await ingest_ohlcv(
            _FakePool(), _FakeExchange(), "BTC/USDT", "1h", 1_578_300_000_000, 3
        )
    message = str(err.value)
    assert "alembic upgrade head" in message
    assert "ohlcv" in message
