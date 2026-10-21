import uuid
from datetime import datetime

import msgspec


class AddProductResponse(msgspec.Struct, frozen=True, gc=False):
    guid: uuid.UUID
    name: str
    slug: str
    price_cents: int
    created_at: datetime
    updated_at: datetime
    description: str | None = None
