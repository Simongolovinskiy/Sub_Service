import uuid
from datetime import datetime
from typing import Protocol, AsyncIterator

from app.domain.dto.product import Product


class ProductRepositoryPort(Protocol):
    async def add(self, product: Product) -> None: ...

    async def get_by_guid(self, guid: uuid.UUID) -> Product | None: ...

    async def find_by_slug(self, slug: str) -> Product | None: ...

    async def list_newer_than(
        self,
        cursor: datetime | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Product]: ...  # Keyset pagination, no offset

    async def list_older_than(
        self,
        cursor: datetime | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Product]: ...

    async def update(self, product: Product) -> None: ...

    async def delete(self, guid: uuid.UUID) -> None: ...
