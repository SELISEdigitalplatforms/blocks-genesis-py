import asyncio
import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import Optional

import aio_pika
from pydantic import BaseModel

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._message.consumer_message import ConsumerMessage
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_client import MessageClient
from blocks_genesis._message.message_configuration import MessageConfiguration
from blocks_genesis._message.rabbit_mq.rabbit_mq_service import RabbitMqService

logger = logging.getLogger(__name__)


class RabbitMessageClient(MessageClient):
    """
    Singleton RabbitMQ message publisher.
    Mirrors AzureMessageClient — call initialize() once at startup,
    then use get_instance() wherever publishing is needed.
    """

    _instance: Optional["RabbitMessageClient"] = None
    _singleton_lock = threading.Lock()

    def __init__(self, message_config: MessageConfiguration):
        self._message_config = message_config
        self._rabbit_mq_service = RabbitMqService(message_config)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Singleton lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def initialize(cls, message_config: MessageConfiguration) -> None:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(message_config)
                MessageClient.set_active_instance(cls._instance)
                logger.info("RabbitMessageClient singleton initialized.")

    @classmethod
    def get_instance(cls) -> "RabbitMessageClient":
        if cls._instance is None:
            raise RuntimeError("RabbitMessageClient not initialized. Call initialize() first.")
        return cls._instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_initialized_async(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._rabbit_mq_service.create_connection_async()
            channel = self._rabbit_mq_service.channel
            channel.return_callbacks.add(self._on_message_returned)
            await self._rabbit_mq_service.initialize_subscriptions_async()
            self._initialized = True

    def _on_message_returned(self, *args) -> None:
        message = args[-1]  # CallbackCollection passes (collection, message)
        logger.warning(
            "Message returned: exchange=%s, routing_key=%s, body=%s",
            message.exchange,
            message.routing_key,
            message.body.decode("utf-8") if message.body else "",
        )

    def _serialize_payload(self, payload) -> dict:
        if isinstance(payload, BaseModel):
            return payload.model_dump()
        elif is_dataclass(payload):
            return asdict(payload)
        elif isinstance(payload, dict):
            return payload
        elif isinstance(payload, str):
            return {"message": payload}
        else:
            raise TypeError(f"Unsupported payload type: {type(payload)}")

    async def _send_message_async(
        self, consumer_message: ConsumerMessage, is_exchange: bool = False
    ) -> bool:
        await self._ensure_initialized_async()

        security_context = BlocksContextManager.get_context()

        with Activity("messaging.rabbitmq.send") as activity:
            activity.set_properties({
                "messaging.system": "rabbitmq",
                "messaging.destination.name": consumer_message.consumer_name,
                "messaging.destination.kind": "exchange" if is_exchange else "queue",
                "messaging.rabbitmq.routing_key": consumer_message.routing_key or "",
                "messaging.message_type": consumer_message.payload_type,
            })

            payload_dict = self._serialize_payload(consumer_message.payload)
            message_body = EventMessage(
                body=json.dumps(payload_dict),
                type=consumer_message.payload_type,
            )

            headers = {
                "TenantId": security_context.tenant_id if security_context else "",
                "TraceId": Activity.get_trace_id(),
                "SpanId": Activity.get_span_id(),
                "SecurityContext": consumer_message.context or json.dumps(
                    security_context.model_dump(mode="json") if security_context else {}
                ),
                "Baggage": json.dumps(activity.get_all_root_attributes()),
            }

            properties: dict = {
                "delivery_mode": aio_pika.DeliveryMode.PERSISTENT,
                "headers": headers,
            }

            ttl = self._message_config.rabbit_mq_configuration.message_ttl_seconds
            if ttl and ttl > 0:
                properties["expiration"] = timedelta(seconds=ttl)

            channel = self._rabbit_mq_service.channel
            message = aio_pika.Message(
                body=json.dumps(message_body.model_dump()).encode(),
                **properties,
            )

            try:
                if is_exchange:
                    exchange = await channel.get_exchange(consumer_message.consumer_name)
                    await exchange.publish(
                        message,
                        routing_key=consumer_message.routing_key or "",
                        mandatory=True,
                    )
                else:
                    await channel.default_exchange.publish(
                        message,
                        routing_key=consumer_message.consumer_name,
                        mandatory=True,
                    )

                logger.info(
                    "Message published to %s (is_exchange=%s) with routing_key=%s",
                    consumer_message.consumer_name,
                    is_exchange,
                    consumer_message.routing_key or "",
                )
                return True
            except Exception as ex:
                logger.error(
                    "Failed to publish message to %s: %s",
                    consumer_message.consumer_name,
                    str(ex),
                )
                raise

    # ------------------------------------------------------------------
    # MessageClient interface
    # ------------------------------------------------------------------

    async def send_to_consumer_async(self, consumer_message: ConsumerMessage) -> bool:
        return await self._send_message_async(consumer_message, is_exchange=False)

    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage) -> bool:
        return await self._send_message_async(consumer_message, is_exchange=True)

    async def close(self) -> None:
        await self._rabbit_mq_service.close()
        self._initialized = False
