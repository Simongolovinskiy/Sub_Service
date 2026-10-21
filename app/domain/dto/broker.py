from typing import Any
from msgspec import Struct


class BrokerMessage(Struct):
    body: Any
    routing_key: str
    delivery_tag: Any
    raw: Any = None
