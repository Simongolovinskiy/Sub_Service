import uuid
from datetime import datetime

from msgspec import Struct, field

from app.domain.common.timezone import get_current_datetime


class Product(Struct, frozen=True, gc=False, kw_only=True):
    guid: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str
    slug: str
    price_cents: int
    description: str | None = None
    created_at: datetime = field(default_factory=get_current_datetime)
    updated_at: datetime = field(default_factory=get_current_datetime)
