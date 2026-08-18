import asyncpg

from trading.config import Settings


async def get_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(settings.postgres_url)
