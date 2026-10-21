from __future__ import annotations

import uuid

from .base import DomainError


class ProductNotFoundError(DomainError):
    """
    Product not found — domain error
    code=404
    """
    def __init__(self, guid: uuid.UUID | None = None):
        super().__init__(
            message=f"Product with guid={guid} not found",
            code=404,
            details={"guid": str(guid)},
        )
