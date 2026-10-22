from datetime import datetime
from uuid import UUID

from msgspec import Struct


class RegisterUserResponse(Struct, frozen=True, gc=False):
    id: UUID
    registered_at: datetime
