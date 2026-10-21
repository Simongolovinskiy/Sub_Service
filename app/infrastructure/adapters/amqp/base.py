from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence

from app.domain.dto.broker import BrokerMessage
from app.infrastructure.ports.amqp import MessageBrokerPort


class BaseMessageBroker(MessageBrokerPort):
    async def __aenter__(self) -> "MessageBrokerPort":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @asynccontextmanager
    async def consumer(
        self,
        routing_key: str,
        prefetch_count: int = 1,
    ) -> AsyncIterator[BrokerMessage]:
        consumer = self.consume(routing_key, prefetch_count=prefetch_count)
        async with consumer:
            yield consumer

    @asynccontextmanager
    async def multi_consumer(
        self,
        routing_keys: Sequence[str],
        prefetch_count: int = 1,
    ) -> AsyncIterator[tuple[str, BrokerMessage]]:
        consumer = self.consume_many(routing_keys, prefetch_count=prefetch_count)
        async with consumer:
            yield consumer
