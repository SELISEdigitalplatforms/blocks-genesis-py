import redis
from fastapi_blocks.core.config import settings

class RedisClient:
    _client = None

    @classmethod
    def get_client(cls):
        if cls._client is None:
            cls._client = redis.Redis.from_url(settings.REDIS_URL)
        return cls._client
