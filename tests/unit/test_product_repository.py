"""Unit tests for Product Repository."""
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.domain.dto.product import Product
from app.infrastructure.adapters.persistence.rdb.repositories.product import (
    RDBProductRepository,
)


@pytest.fixture
def mock_connection():
    """Mock asyncpg connection."""
    conn = AsyncMock()
    return conn


@pytest.fixture
def repository(mock_connection):
    """Create repository instance with mocked connection."""
    return RDBProductRepository(mock_connection)


@pytest.fixture
def sample_product():
    """Sample product for testing."""
    return Product(
        guid=uuid.uuid4(),
        name="Test Product",
        slug="test-product",
        price_cents=9999,
        description="Test description",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestProductRepository:
    """Test suite for RDBProductRepository."""

    @pytest.mark.asyncio
    async def test_add_product(self, repository, mock_connection, sample_product):
        """Test adding a product to the database."""
        # Arrange
        mock_connection.execute = AsyncMock()

        # Act
        await repository.add(sample_product)

        # Assert
        mock_connection.execute.assert_called_once()
        call_args = mock_connection.execute.call_args
        assert "INSERT INTO products" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_by_guid_found(self, repository, mock_connection, sample_product):
        """Test retrieving a product by GUID when it exists."""
        # Arrange
        mock_row = {
            "guid": sample_product.guid,
            "name": sample_product.name,
            "slug": sample_product.slug,
            "price_cents": sample_product.price_cents,
            "description": sample_product.description,
            "created_at": sample_product.created_at,
            "updated_at": sample_product.updated_at,
        }
        mock_connection.fetchrow = AsyncMock(return_value=mock_row)

        # Act
        result = await repository.get_by_guid(sample_product.guid)

        # Assert
        assert result is not None
        assert result.guid == sample_product.guid
        assert result.name == sample_product.name

    @pytest.mark.asyncio
    async def test_get_by_guid_not_found(self, repository, mock_connection):
        """Test retrieving a product by GUID when it doesn't exist."""
        # Arrange
        mock_connection.fetchrow = AsyncMock(return_value=None)

        # Act
        result = await repository.get_by_guid(uuid.uuid4())

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_product(self, repository, mock_connection):
        """Test deleting a product."""
        # Arrange
        product_guid = uuid.uuid4()
        mock_connection.execute = AsyncMock()

        # Act
        await repository.delete(product_guid)

        # Assert
        mock_connection.execute.assert_called_once()
        call_args = mock_connection.execute.call_args
        assert "DELETE FROM products" in call_args[0][0]


@pytest.mark.asyncio
async def test_naive_utc_product_timestamp_is_preserved(repository, mock_connection, sample_product, monkeypatch):
    import os
    import time

    import msgspec

    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Moscow")
    time.tzset()
    timestamp = datetime(2026, 1, 1, 12)
    try:
        product = msgspec.structs.replace(sample_product, created_at=timestamp, updated_at=timestamp)
        await repository.add(product)
        assert mock_connection.execute.call_args.args[6:] == (timestamp, timestamp)
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
