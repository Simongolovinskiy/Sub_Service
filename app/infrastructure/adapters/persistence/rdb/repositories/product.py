# app/infrastructure/persistence/repositories/product.py
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg

from app.domain.dto.product import Product
from app.domain.ports.repositories.product import ProductRepositoryPort


class RDBProductRepository(ProductRepositoryPort):
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def add(self, product: Product) -> None:
        query = """
            INSERT INTO products (guid, name, slug, price_cents, description, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """
        await self._conn.execute(
            query,
            product.guid,
            product.name,
            product.slug,
            product.price_cents,
            product.description,
            self._db_timestamp(product.created_at),
            self._db_timestamp(product.updated_at),
        )

    async def get_by_guid(self, guid: uuid.UUID) -> Product | None:
        query = """
            SELECT guid, name, slug, price_cents, description, created_at, updated_at
            FROM products WHERE guid = $1
        """
        row = await self._conn.fetchrow(query, guid)
        return self._row_to_entity(row) if row else None

    async def find_by_slug(self, slug: str) -> Product | None:
        query = """
            SELECT guid, name, slug, price_cents, description, created_at, updated_at
            FROM products WHERE slug = $1
        """
        row = await self._conn.fetchrow(query, slug)
        return self._row_to_entity(row) if row else None

    async def list_newer_than(
        self, cursor: datetime | None = None, limit: int = 100
    ) -> AsyncIterator[Product]:
        if cursor is None:
            query = """
                SELECT guid, name, slug, price_cents, description, created_at, updated_at
                FROM products ORDER BY created_at DESC LIMIT $1
            """
            rows = await self._conn.fetch(query, limit)
        else:
            query = """
                SELECT guid, name, slug, price_cents, description, created_at, updated_at
                FROM products
                WHERE created_at > $1 ORDER BY created_at DESC LIMIT $2
            """
            rows = await self._conn.fetch(query, self._db_timestamp(cursor), limit)

        for row in rows:
            yield self._row_to_entity(row)

    async def list_older_than(
        self, cursor: datetime | None = None, limit: int = 100
    ) -> AsyncIterator[Product]:
        if cursor is None:
            query = """
                SELECT guid, name, slug, price_cents, description, created_at, updated_at
                FROM products ORDER BY created_at ASC LIMIT $1
            """
            rows = await self._conn.fetch(query, limit)
        else:
            query = """
                SELECT guid, name, slug, price_cents, description, created_at, updated_at
                FROM products
                WHERE created_at < $1 ORDER BY created_at ASC LIMIT $2
            """
            rows = await self._conn.fetch(query, self._db_timestamp(cursor), limit)

        for row in rows:
            yield self._row_to_entity(row)

    async def update(self, product: Product) -> None:
        now = datetime.now(tz=UTC).replace(tzinfo=None)
        query = """
            UPDATE products SET
                name = $2, slug = $3, price_cents = $4, description = $5, updated_at = $6
            WHERE guid = $1
        """
        await self._conn.execute(query,
                                 product.guid, product.name, product.slug,
                                 product.price_cents, product.description, now
                                 )

    async def delete(self, guid: uuid.UUID) -> None:
        await self._conn.execute("DELETE FROM products WHERE guid = $1", guid)

    @staticmethod
    def _db_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    @staticmethod
    def _row_to_entity(row: asyncpg.Record) -> Product:
        return Product(
            guid=row["guid"],
            name=row["name"],
            slug=row["slug"],
            price_cents=row["price_cents"],
            description=row["description"],
            created_at=row["created_at"].replace(tzinfo=UTC),
            updated_at=row["updated_at"].replace(tzinfo=UTC),
        )
