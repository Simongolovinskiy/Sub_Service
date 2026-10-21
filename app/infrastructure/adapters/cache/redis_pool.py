"""Redis connection pool and cache implementation."""
from typing import Any, AsyncGenerator
import json

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from app.domain.core.config.settings import CacheConfig
from app.infrastructure.adapters._logging import get_logger
from app.infrastructure.ports.product_cache import ProductCachePort

logger = get_logger()


class RedisPool:
    """Redis connection pool manager."""

    def __init__(self, config: CacheConfig):
        self.config = config
        self.pool: ConnectionPool | None = None
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Initialize Redis connection pool."""
        self.pool = ConnectionPool(
            host=self.config.host,
            port=self.config.port,
            db=self.config.db,
            password=self.config.password,
            decode_responses=self.config.decode_responses,
            max_connections=50,  # Highload optimization
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        self._client = aioredis.Redis(connection_pool=self.pool)

        # Test connection
        await self._client.ping()
        logger.info("Redis connection pool initialized", host=self.config.host, port=self.config.port)

    async def disconnect(self) -> None:
        """Close Redis connection pool."""
        if self._client:
            await self._client.aclose()
            logger.info("Redis connection pool closed")

    def get_client(self) -> aioredis.Redis:
        """Get Redis client from pool."""
        if not self._client:
            raise RuntimeError("Redis pool not initialized. Call connect() first.")
        return self._client


class RedisCache:
    """Generic Redis cache implementation."""

    def __init__(self, client: aioredis.Redis, default_ttl: int = 3600):
        self.client = client
        self.default_ttl = default_ttl

    async def get(self, key: str) -> Any | None:
        """Get value from cache."""
        try:
            value = await self.client.get(key)
            if value:
                return json.loads(value) if isinstance(value, str) else value
            return None
        except Exception as e:
            logger.error("Cache get error", key=key, error=str(e))
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Set value in cache with TTL."""
        try:
            ttl = ttl or self.default_ttl
            serialized = json.dumps(value) if not isinstance(value, (str, bytes)) else value
            await self.client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            logger.error("Cache set error", key=key, error=str(e))
            return False

    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        try:
            await self.client.delete(key)
            return True
        except Exception as e:
            logger.error("Cache delete error", key=key, error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        try:
            return await self.client.exists(key) > 0
        except Exception as e:
            logger.error("Cache exists error", key=key, error=str(e))
            return False

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment counter."""
        return await self.client.incrby(key, amount)

    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiration time for key."""
        return await self.client.expire(key, ttl)
