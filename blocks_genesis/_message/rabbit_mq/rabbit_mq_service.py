import logging

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection

from blocks_genesis._message.message_configuration import MessageConfiguration

logger = logging.getLogger(__name__)


class RabbitMqService:
    """
    Manages the aio_pika connection and channel for RabbitMQ.
    Handles queue/exchange declaration and bindings based on MessageConfiguration.
    """

    def __init__(self, config: MessageConfiguration):
        self._config = config
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None

    @property
    def channel(self) -> AbstractChannel:
        if self._channel is None:
            raise RuntimeError("RabbitMQ channel has not been initialized. Call create_connection_async() first.")
        return self._channel

    async def create_connection_async(self):
        """Establishes a robust (auto-recovering) connection and opens a channel."""
        try:
            self._connection = await aio_pika.connect_robust(
                self._config.connection,
                timeout=62,
            )
            self._channel = await self._connection.channel()
            logger.info("Successfully established RabbitMQ connection and channel.")
        except Exception:
            logger.exception("An error occurred while creating the RabbitMQ connection.")
            raise

    async def initialize_subscriptions_async(self):
        """
        Declares queues, exchanges, and bindings for every ConsumerSubscription
        defined in the RabbitMqConfiguration.
        """
        if self._channel is None:
            raise RuntimeError("RabbitMQ channel is not initialized.")

        rabbit_config = self._config.rabbit_mq_configuration
        if not rabbit_config:
            return

        for subscription in rabbit_config.consumer_subscriptions:
            queue = await self._channel.declare_queue(
                name=subscription.queue_name,
                durable=subscription.durable,
                auto_delete=False,
            )

            if subscription.exchange_name:
                exchange_type = aio_pika.ExchangeType(subscription.exchange_type)
                exchange = await self._channel.declare_exchange(
                    name=subscription.exchange_name,
                    type=exchange_type,
                    durable=subscription.durable,
                    auto_delete=False,
                )
                await queue.bind(exchange=exchange, routing_key=subscription.routing_key)

            await self._channel.set_qos(prefetch_count=subscription.prefetch_count)
            logger.info(
                "RabbitMQ subscription initialized: queue=%s, exchange=%s, routing_key=%s, prefetch=%d",
                subscription.queue_name,
                subscription.exchange_name or "(direct)",
                subscription.routing_key or "",
                subscription.prefetch_count,
            )

        logger.info("RabbitMQ subscriptions initialized successfully.")

    async def close(self):
        """Gracefully closes the channel and connection."""
        if self._channel and not self._channel.is_closed:
            try:
                await self._channel.close()
            except Exception:
                logger.warning("Error while closing RabbitMQ channel.", exc_info=True)

        if self._connection and not self._connection.is_closed:
            try:
                await self._connection.close()
            except Exception:
                logger.warning("Error while closing RabbitMQ connection.", exc_info=True)
