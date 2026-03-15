import asyncio
import json
import logging
from typing import List, Optional

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._message.consumer import Consumer
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_configuration import ConsumerSubscription, MessageConfiguration
from blocks_genesis._message.rabbit_mq.rabbit_mq_service import RabbitMqService

logger = logging.getLogger(__name__)


class RabbitMessageWorker:
    """
    Async RabbitMQ consumer worker.
    Mirrors the .NET RabbitMessageWorker — uses push-based consuming
    with explicit ACK after processing (autoAck=false).
    """

    def __init__(self, message_config: MessageConfiguration):
        self._message_config = message_config
        self._consumer = Consumer()
        self._rabbit_mq_service: Optional[RabbitMqService] = None
        self._tracer = trace.get_tracer(__name__)
        self._stop_event = asyncio.Event()

    def initialize(self) -> None:
        """Creates the RabbitMqService (connection is established during run())."""
        if not self._message_config.connection:
            raise ValueError("RabbitMQ connection string is missing in MessageConfiguration.")
        self._rabbit_mq_service = RabbitMqService(self._message_config)
        logger.info("RabbitMQ service created and ready to connect.")

    async def run(self) -> None:
        """Connects, declares queues/exchanges, and starts consuming all subscriptions."""
        if self._rabbit_mq_service is None:
            raise RuntimeError("RabbitMessageWorker is not initialized. Call initialize() first.")

        await self._rabbit_mq_service.create_connection_async()
        await self._rabbit_mq_service.initialize_subscriptions_async()

        channel = self._rabbit_mq_service.channel
        rabbit_config = self._message_config.rabbit_mq_configuration

        if not rabbit_config or not rabbit_config.consumer_subscriptions:
            logger.warning("No consumer subscriptions configured for RabbitMQ.")
            return

        await self._start_consuming(channel, rabbit_config.consumer_subscriptions)

        logger.info("RabbitMQ worker is running and awaiting messages.")
        # Block until stop() is called
        await self._stop_event.wait()

    async def _start_consuming(
        self, channel: aio_pika.abc.AbstractChannel, subscriptions: List[ConsumerSubscription]
    ) -> None:
        """
        Registers a push-based consumer on each queue.
        Mirrors .NET StartConsumingAsync — uses BasicConsume with autoAck=false.
        """
        for subscription in subscriptions:
            queue = await channel.declare_queue(
                name=subscription.queue_name,
                durable=subscription.durable,
                auto_delete=False,
            )
            msg_count = queue.declaration_result.message_count
            logger.info(
                "Queue '%s' declared — %d message(s) pending.",
                subscription.queue_name,
                msg_count,
            )

            # Set prefetch per queue (mirrors .NET BasicQos)
            await channel.set_qos(prefetch_count=subscription.prefetch_count)

            # Register push-based consumer (no_ack=False → explicit ACK required)
            # IMPORTANT: must use a proper async function, NOT a lambda.
            # aio_pika checks asyncio.iscoroutinefunction() — lambdas returning
            # coroutines fail this check and the coroutine is never awaited.
            consumer_tag = await queue.consume(
                callback=self._make_callback(subscription),
                no_ack=False,
            )

            logger.info(
                "Started consuming queue: %s, consumer_tag=%s, parallel=%s, prefetch=%d",
                subscription.queue_name,
                consumer_tag,
                subscription.parallel_processing,
                subscription.prefetch_count,
            )

    def _make_callback(self, subscription: ConsumerSubscription):
        """
        Creates a proper async callback bound to the given subscription.
        This is necessary because aio_pika uses asyncio.iscoroutinefunction()
        to decide whether to await the callback — lambdas fail that check.
        """
        async def callback(message: AbstractIncomingMessage) -> None:
            try:
                if subscription.parallel_processing:
                    asyncio.create_task(self._process_message(message, subscription))
                else:
                    await self._process_message(message, subscription)
            except Exception:
                logger.exception("Unhandled error in message callback for queue '%s'", subscription.queue_name)
        return callback

    async def _process_message(
        self, message: AbstractIncomingMessage, subscription: ConsumerSubscription
    ) -> None:
        """Handles a single incoming message: context → tracing → dispatch → always ACK."""
        headers = message.headers or {}

        def _decode(val) -> str:
            if isinstance(val, bytes):
                return val.decode("utf-8")
            return str(val) if val is not None else ""

        trace_id = _decode(headers.get("TraceId", ""))
        span_id = _decode(headers.get("SpanId", ""))
        tenant_id = _decode(headers.get("TenantId", ""))
        security_context_raw = _decode(headers.get("SecurityContext", ""))
        baggage_str = _decode(headers.get("Baggage", "{}"))

        # Restore security context (mirrors .NET: BlocksContext.SetContext)
        if security_context_raw:
            try:
                sc = json.loads(security_context_raw)
                BlocksContextManager.set_context(BlocksContextManager.create(**sc))
            except Exception:
                logger.warning("Could not parse SecurityContext header.", exc_info=True)

        # Restore trace context
        context = None
        if trace_id and span_id:
            try:
                context = TraceContextTextMapPropagator().extract(
                    {"traceparent": f"00-{trace_id}-{span_id}-01"}
                )
            except Exception:
                logger.warning("Could not extract trace context from headers.", exc_info=True)

        with self._tracer.start_as_current_span(
            "process.messaging.rabbitmq",
            context=context,
            kind=SpanKind.CONSUMER,
        ) as span:
            span.set_attribute("messaging.system", "rabbitmq")
            span.set_attribute("messaging.destination.name", subscription.queue_name)
            span.set_attribute("SecurityContext", security_context_raw)
            span.set_attribute("baggage.TenantId", tenant_id)
            span.set_attribute("usage", True)

            try:
                baggages = json.loads(baggage_str)
                for key, value in baggages.items():
                    span.set_attribute(f"baggage.{key}", str(value))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid baggage JSON in message headers.")

            body_bytes = message.body
            body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes)
            span.set_attribute("message.body", body_str)
            logger.info(
                "Received RabbitMQ message on queue '%s'. Body: %s",
                subscription.queue_name,
                body_str,
            )

            try:
                event = EventMessage(**json.loads(body_str))
                await self._consumer.process_message(event.type, event.body)
                span.set_status(StatusCode.OK, "Message processed successfully")
                logger.info("Message processed successfully.")
            except Exception as ex:
                logger.exception("Error processing RabbitMQ message.")
                span.set_status(StatusCode.ERROR, str(ex))
                span.set_attribute("error", str(ex))
            finally:
                # Always ACK — mirrors .NET: BasicAckAsync in finally block
                await message.ack()
                BlocksContextManager.clear_context()

    async def stop(self) -> None:
        """Signals the worker to stop and closes the RabbitMQ connection."""
        self._stop_event.set()

        if self._rabbit_mq_service:
            await self._rabbit_mq_service.close()

        logger.info("RabbitMessageWorker stopped.")
