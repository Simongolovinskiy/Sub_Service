from uuid import UUID

from msgspec import Struct


class AddUserResponse(Struct, frozen=True, gc=False):
    guid: UUID
