from uuid import UUID

from msgspec import Struct


class ExtendSubscriptionRequest(Struct, frozen=True, gc=False):
    user_guid: UUID
    idempotency_key: str
