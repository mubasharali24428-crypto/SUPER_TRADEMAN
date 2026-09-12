"""PostgreSQL connection management.

The schema itself is owned by Alembic migrations (see ``alembic/versions/``);
runtime code never issues DDL. Apply the schema with::

    POSTGRES_URL=postgresql://... .venv/bin/alembic upgrade head
"""

import os
from typing import Optional

import asyncpg


def resolve_postgres_url(settings=None) -> str:
    """Resolve a Postgres URL from an optional Settings object or the environment.

    Precedence: Settings.postgres_url -> POSTGRES_URL env -> DATABASE_URL env.
    Raises RuntimeError when nothing is configured (no credential defaults are
    ever embedded here, matching the policy in trading.config).
    """
    if settings is not None:
        return settings.postgres_url
    url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No Postgres URL available: pass Settings or set POSTGRES_URL "
            "(or DATABASE_URL) in the environment."
        )
    return url


async def get_pool(
    settings=None, min_size: int = 1, max_size: int = 10
) -> asyncpg.Pool:
    """Create an asyncpg connection pool from resolved configuration."""
    return await asyncpg.create_pool(
        resolve_postgres_url(settings), min_size=min_size, max_size=max_size
    )


__all__ = ["get_pool", "resolve_postgres_url"]
