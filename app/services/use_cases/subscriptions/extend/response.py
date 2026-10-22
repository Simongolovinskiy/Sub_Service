from datetime import datetime
from typing import Literal

from msgspec import Struct


class ExtendSubscriptionResponse(Struct, frozen=True, gc=False):
    subscription_url: str
    expires_at: datetime
    status: Literal['pending', 'active', 'disabled']
