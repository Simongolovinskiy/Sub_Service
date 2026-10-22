from uuid import UUID

from msgspec import Struct


class CreateSubscriptionRequest(Struct, frozen=True, gc=False):
    user_guid: UUID
    idempotency_key: str
