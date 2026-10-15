import logging
from typing import Optional
from datetime import timedelta
from azure.servicebus.management import ServiceBusAdministrationClient
from blocks_genesis._message.message_configuration import MessageConfiguration

logger = logging.getLogger(__name__)


class ConfigAzureServiceBus:
    _admin_client: Optional[ServiceBusAdministrationClient] = None
    _message_config: Optional[MessageConfiguration] = None

    @classmethod
    def configure_queue_and_topic(cls, message_config: MessageConfiguration):
        try:
            cls._admin_client = ServiceBusAdministrationClient.from_connection_string(
                message_config.connection
            )
            cls._message_config = message_config

            cls._create_queues()
            cls._create_topics_and_subscriptions()

        except Exception as ex:
            # Only the type: the message can name the namespace.
            logger.debug("Exception during Service Bus configuration: %s", type(ex).__name__)
            raise

    @classmethod
    def _create_queues(cls):
        config = cls._message_config.azure_service_bus_configuration
        queues = config.queues or []

        for queue_name in queues:
            if cls._check_queue_exists(queue_name):
                logger.debug("Queue '%s' already exists. Skipping creation.", queue_name)
                continue

            cls._admin_client.create_queue(
                queue_name,
                max_size_in_megabytes=config.queue_max_size_in_megabytes,
                max_delivery_count=config.queue_max_delivery_count,
                default_message_time_to_live=config.queue_default_message_time_to_live, 
                lock_duration=timedelta(seconds=300),  # 5 minutes
            )
            logger.debug("Queue created: %s", queue_name)

    @classmethod
    def _check_queue_exists(cls, queue_name: str) -> bool:
        try:
            cls._admin_client.get_queue(queue_name)
            return True
        except Exception:
            return False

    @classmethod
    def _create_topics_and_subscriptions(cls):
        config = cls._message_config.azure_service_bus_configuration
        topics = config.topics or []

        for topic_name in topics:
            if cls._check_topic_exists(topic_name):
                logger.debug("Topic '%s' already exists. Skipping creation.", topic_name)
            else:
                cls._admin_client.create_topic(
                    topic_name,
                    max_size_in_megabytes=config.topic_max_size_in_megabytes,
                    default_message_time_to_live=config.topic_default_message_time_to_live,
                )
                logger.debug("Topic created: %s", topic_name)

            cls._create_subscription(topic_name)

    @classmethod
    def _check_topic_exists(cls, topic_name: str) -> bool:
        try:
            cls._admin_client.get_topic(topic_name)
            return True
        except Exception:
            return False

    @classmethod
    def _create_subscription(cls, topic_name: str):
        config = cls._message_config.azure_service_bus_configuration
        subscription_name = cls._message_config.get_subscription_name(topic_name)

        if cls._check_subscription_exists(topic_name, subscription_name):
            logger.debug("Subscription '%s' for topic '%s' already exists. Skipping.", subscription_name, topic_name)
            return

        cls._admin_client.create_subscription(
            topic_name,
            subscription_name,
            max_delivery_count=config.topic_subscription_max_delivery_count,
            default_message_time_to_live=config.topic_subscription_default_message_time_to_live,
            lock_duration=timedelta(seconds=300),
        )
        logger.debug("Subscription '%s' created for topic '%s'", subscription_name, topic_name)

    @classmethod
    def _check_subscription_exists(cls, topic_name: str, subscription_name: str) -> bool:
        try:
            cls._admin_client.get_subscription(topic_name, subscription_name)
            return True
        except Exception:
            return False
