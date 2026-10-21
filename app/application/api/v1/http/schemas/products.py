"""Product API schemas (HTTP layer DTOs)."""
import uuid

from pydantic import BaseModel


class CreateProductRequest(BaseModel):
    """API request schema for creating a product."""
    name: str
    slug: str
    price_cents: int
    description: str | None = None


class ProductResponse(BaseModel):
    """API response schema for product."""
    guid: uuid.UUID
    name: str
    slug: str
    price_cents: int
    description: str | None
    created_at: str  # ISO format string for API
    updated_at: str  # ISO format string for API
