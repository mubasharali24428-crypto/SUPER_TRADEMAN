"""Alembic environment for the algo-trading-system Postgres schema.

Async engine (asyncpg) wired to the POSTGRES_URL environment variable
(DATABASE_URL accepted as a fallback). The database URL is intentionally NOT
committed here -- credentials come from the environment only.
"""

import asyncio
import os
# Make ``src`` importable so trading.* settings could be used if ever needed.
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """Resolve the target database URL from the environment.

    POSTGRES_URL is the canonical variable used by trading.config.Settings;
    DATABASE_URL is accepted as a widely-used fallback. No credentials are
    ever committed to this repository.
    """
    url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Alembic needs a database URL: set POSTGRES_URL (or DATABASE_URL) "
            "in the environment before running migrations."
        )
    # asyncpg driver for runtime migrations; plain postgresql:// URLs are
    # upgraded transparently.
    #
    # VA-049: accept BOTH libpq spellings ("postgresql://" and the legacy
    # "postgres://", which asyncpg would otherwise choke on mid-migration),
    # then validate against the supported scheme set so unsupported drivers
    # fail here with a clear message instead of deep inside SQLAlchemy during
    # the migration window.
    if url.startswith(("postgresql://", "postgres://")):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )
    supported = ("postgresql+asyncpg://", "sqlite://")
    if not url.startswith(supported):
        raise RuntimeError(
            f"Unsupported database URL scheme in POSTGRES_URL/DATABASE_URL "
            f"(must be postgresql://, postgres://, or sqlite://): {url.split('://')[0]}://"
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (--sql)."""
    context.configure(
        url=_database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
