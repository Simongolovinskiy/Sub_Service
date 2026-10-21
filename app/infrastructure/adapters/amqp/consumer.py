import asyncio
import logging
from typing import Optional, Callable, Awaitable

from app.domain.dto.broker import BrokerMessage
from app.infrastructure.ports.amqp import MessageBrokerPort

logger = logging.getLogger(__name__)


class MessageWorker:
    def __init__(
        self,
        broker: MessageBrokerPort,
        topics: list[str],
        prefetch_count: int = 10,
    ):
        self.broker = broker
        self.topics = topics
        self.prefetch_count = prefetch_count

        self._worker_task: Optional[asyncio.Task] = None
        self._handlers: dict[str, Callable[[BrokerMessage], Awaitable[None]]] = {}

    def register_handler(
        self,
        topic: str,
        handler: Callable[[BrokerMessage], Awaitable[None]],
    ) -> None:
        self._handlers[topic] = handler
        logger.info(f"Handler registered for topic: {topic}")

    async def start(self) -> None:
        await self.broker.start()
        self._worker_task = asyncio.create_task(self._consume_loop())
        logger.info(f"MessageWorker started, listening to topics: {self.topics}")

    async def stop(self) -> None:
        logger.info("Stopping MessageWorker...")

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                logger.info("Consumer loop cancelled")

        await self.broker.close()
        logger.info("MessageWorker stopped")

    async def _consume_loop(self) -> None:
        try:
            async for topic, message in await self.broker.consume_many(
                self.topics,
                prefetch_count=self.prefetch_count,
            ):
                try:
                    await self._handle_message(topic, message)
                except Exception as e:
                    logger.error(
                        f"Error processing message from {topic}: {e}",
                        exc_info=True,
                    )
                    await self.broker.nack(message)

        except asyncio.CancelledError:
            logger.info("Consumer loop cancelled")
            raise

    async def _handle_message(
        self,
        topic: str,
        message: BrokerMessage,
    ) -> None:
        if topic not in self._handlers:
            logger.warning(f"No handler registered for topic: {topic}, nacking")
            await self.broker.nack(message)
            return

        handler = self._handlers[topic]
        await handler(message)
        await self.broker.ack(message)
