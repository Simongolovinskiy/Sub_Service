import uuid

from typing import Protocol, Optional

from app.domain.dto.product import Product


class ProductCachePort(Protocol):
    async def put(self, *, product_guid: uuid.UUID, product: Product) -> None:
        raise NotImplementedError

    async def get(self, product_guid: uuid.UUID) -> Optional[Product]:
        raise NotImplementedError

    async def delete(self, product_guid: uuid.UUID) -> None:
        raise NotImplementedError
