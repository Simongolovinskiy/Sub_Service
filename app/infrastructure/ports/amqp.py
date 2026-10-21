from typing import Protocol, AsyncIterator, Sequence, Any

from app.domain.dto.broker import BrokerMessage


class MessageBrokerPort(Protocol):
    async def start(self) -> None:
        ...

    async def close(self) -> None:
        ...

    async def publish(self, routing_key: str, body: Any, *, headers: dict | None = None) -> None:
        ...

    async def consume(
        self,
        routing_key: str,
        prefetch_count: int = 1,
    ) -> AsyncIterator[BrokerMessage]:
        ...

    async def consume_many(
        self,
        routing_keys: Sequence[str],
        prefetch_count: int = 1,
    ) -> AsyncIterator[tuple[str, BrokerMessage]]:
        ...

    async def ack(self, message: BrokerMessage) -> None:
        ...

    async def nack(self, message: BrokerMessage, *, requeue: bool = True) -> None:
        ...

    async def reject(self, message: BrokerMessage, *, requeue: bool = False) -> None:
        ...
