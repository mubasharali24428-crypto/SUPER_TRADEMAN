import redis.asyncio as redis

from trading.config import Settings


def get_redis(settings: Settings) -> redis.Redis:
    return redis.from_url(settings.redis_url)
