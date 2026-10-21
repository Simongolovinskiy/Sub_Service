import uuid
from datetime import datetime

from msgspec import Struct, field

from app.domain.common.timezone import get_current_datetime


class User(Struct, frozen=True, gc=False, kw_only=True):
    guid: uuid.UUID = field(default_factory=uuid.uuid4)
    username: str
    password: str
    email: str | None = None
    is_active: bool = True
    is_superadmin: bool = False
    created_at: datetime = field(default_factory=get_current_datetime)
