import logging
import pickle
import uuid
from typing import Optional
from redis.asyncio import Redis

from app.domain.common.enums import CacheKey
from app.domain.dto.product import Product
from app.infrastructure.ports.product_cache import ProductCachePort

logger = logging.getLogger(__name__)


class RedisProductCache(ProductCachePort):
    def __init__(self, redis_client: Redis = None) -> None:
        self._redis = redis_client
        self._base_key = CacheKey.PRODUCT.value

    def _make_key(self, product_guid: uuid.UUID) -> str:
        return f"{self._base_key}{product_guid.hex}"

    async def put(self, *, product_guid: uuid.UUID, product: Product) -> None:
        value = pickle.dumps(product)
        await self._redis.set(self._make_key(product_guid), value, ex=1800)

    async def get(self, product_guid: uuid.UUID) -> Optional[Product]:
        value = await self._redis.get(self._make_key(product_guid))
        return pickle.loads(value) if value else None

    async def delete(self, product_guid: uuid.UUID) -> None:
        await self._redis.delete(self._make_key(product_guid))
