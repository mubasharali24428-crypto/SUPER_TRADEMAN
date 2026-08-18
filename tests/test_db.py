from trading.config import Settings
from trading.db.postgres import get_pool
from trading.db.redis import get_redis


async def test_postgres_connects():
    settings = Settings()
    pool = await get_pool(settings)
    try:
        assert await pool.fetchval("SELECT 1") == 1
    finally:
        await pool.close()


async def test_redis_connects():
    settings = Settings()
    client = get_redis(settings)
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()
