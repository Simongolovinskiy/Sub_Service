from uuid import UUID

from msgspec import Struct


class GetSubscriptionRequest(Struct, frozen=True, gc=False):
    user_guid: UUID
