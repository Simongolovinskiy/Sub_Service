from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence, Any
import asyncio
import logging

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.structs import ConsumerRecord

from app.domain.dto.broker import BrokerMessage
from app.infrastructure.adapters.amqp.base import BaseMessageBroker

logger = logging.getLogger(__name__)


class KafkaMessageBroker(BaseMessageBroker):
    def __init__(
        self,
        bootstrap_servers: str | list[str],
        consumer_group_id: str,
        security_config: dict | None = None,
        producer_acks: str = "all",
        session_timeout_ms: int = 30000,
        heartbeat_interval_ms: int = 10000,
        rebalance_timeout_ms: int = 60000,
        shutdown_timeout_sec: float = 30.0,
        **kafka_kwargs,
    ):
        self.bootstrap_servers = bootstrap_servers
        self.consumer_group_id = consumer_group_id
        self.security_config = security_config or {}
        self.producer_acks = producer_acks
        self.kafka_kwargs = kafka_kwargs

        self.session_timeout_ms = session_timeout_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.rebalance_timeout_ms = rebalance_timeout_ms
        self.shutdown_timeout_sec = shutdown_timeout_sec

        self._producer: AIOKafkaProducer | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._active_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._running:
            return

        logger.info("Starting Kafka broker")
        self._shutdown_event.clear()

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            acks=self.producer_acks,
            **self.security_config,
            **self.kafka_kwargs,
        )
        await self._producer.start()
        self._running = True
        logger.info("Kafka broker started successfully")

    async def close(self) -> None:
        if not self._running:
            return

        logger.info("Closing Kafka broker (graceful shutdown)")
        self._running = False
        self._shutdown_event.set()

        await self._wait_for_active_tasks()

        if self._consumer:
            try:
                await asyncio.wait_for(
                    self._stop_consumer_gracefully(),
                    timeout=self.shutdown_timeout_sec
                )
            except asyncio.TimeoutError:
                logger.warning("Consumer shutdown timeout, force stopping")
                await self._consumer.stop()

        if self._producer:
            try:
                await asyncio.wait_for(
                    self._producer.stop(),
                    timeout=self.shutdown_timeout_sec / 2
                )
            except asyncio.TimeoutError:
                logger.warning("Producer shutdown timeout")

        self._consumer = None
        self._producer = None
        logger.info("Kafka broker closed")

    async def _stop_consumer_gracefully(self) -> None:
        if not self._consumer:
            return

        try:
            await self._consumer.commit()
        except Exception as e:
            logger.error(f"Error committing offsets: {e}")
        finally:
            await self._consumer.stop()

    async def _wait_for_active_tasks(self, timeout: float | None = None) -> None:
        if not self._active_tasks:
            return

        timeout = timeout or self.shutdown_timeout_sec * 0.8
        done, pending = await asyncio.wait(
            self._active_tasks,
            timeout=timeout,
            return_when=asyncio.ALL_COMPLETED
        )

        if pending:
            logger.warning(f"{len(pending)} tasks still running, cancelling them")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        self._active_tasks.clear()

    def _track_task(self, task: asyncio.Task) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def publish(
        self,
        routing_key: str,
        body: Any,
        *,
        headers: dict | None = None,
    ) -> None:
        if not self._producer:
            raise RuntimeError("Broker not started")

        if self._shutdown_event.is_set():
            raise RuntimeError("Broker is shutting down, cannot publish")

        data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")

        await self._producer.send_and_wait(
            topic=routing_key,
            value=data,
            headers=[(k, v.encode() if isinstance(v, str) else v) for k, v in (headers or {}).items()],
        )

    @asynccontextmanager
    async def _single_consumer(self, topic: str, prefetch_count: int) -> AsyncIterator[AIOKafkaConsumer]:
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.consumer_group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_records=prefetch_count,
            session_timeout_ms=self.session_timeout_ms,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
            rebalance_timeout_ms=self.rebalance_timeout_ms,
            **self.security_config,
        )
        await consumer.start()
        try:
            yield consumer
        finally:
            await consumer.stop()

    @asynccontextmanager
    async def _multi_consumer(self, topics: Sequence[str], prefetch_count: int) -> AsyncIterator[AIOKafkaConsumer]:
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.consumer_group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_records=prefetch_count * len(topics),
            session_timeout_ms=self.session_timeout_ms,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
            rebalance_timeout_ms=self.rebalance_timeout_ms,
            **self.security_config,
        )
        await consumer.start()
        try:
            yield consumer
        finally:
            await consumer.stop()

    async def consume(
        self,
        routing_key: str,
        prefetch_count: int = 1,
    ) -> AsyncIterator[BrokerMessage]:
        async with self._single_consumer(routing_key, prefetch_count) as consumer:
            self._consumer = consumer
            async for raw_msg in consumer:
                if self._shutdown_event.is_set():
                    logger.info("Shutdown signal received, stopping consume")
                    break
                yield self._to_broker_message(raw_msg)

    async def consume_many(
        self,
        routing_keys: Sequence[str],
        prefetch_count: int = 1,
    ) -> AsyncIterator[tuple[str, BrokerMessage]]:
        async with self._multi_consumer(routing_keys, prefetch_count) as consumer:
            self._consumer = consumer
            async for raw_msg in consumer:
                if self._shutdown_event.is_set():
                    logger.info("Shutdown signal received, stopping consume_many")
                    break
                yield raw_msg.topic, self._to_broker_message(raw_msg)

    async def ack(self, message: BrokerMessage) -> None:
        if self._consumer is None:
            return
        try:
            await self._consumer.commit()
        except Exception as e:
            logger.error(f"Error committing message: {e}")

    async def nack(self, message: BrokerMessage, *, requeue: bool = True) -> None:
        logger.debug(f"Message nacked (offset will not be committed)")

    async def reject(self, message: BrokerMessage, *, requeue: bool = False) -> None:
        await self.nack(message, requeue=requeue)

    def _to_broker_message(self, raw: ConsumerRecord) -> BrokerMessage:
        body = raw.value.decode("utf-8") if raw.value else None
        delivery_tag = (raw.offset, raw.partition)
        return BrokerMessage(
            body=body,
            routing_key=raw.topic,
            delivery_tag=delivery_tag,
            raw=raw,
        )

    def is_running(self) -> bool:
        return self._running and not self._shutdown_event.is_set()
