from typing import Optional
from datetime import timedelta
from azure.servicebus.management import ServiceBusAdministrationClient
from blocks_genesis._message.message_configuration import MessageConfiguration


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
            print(f"Exception during Service Bus configuration: {ex}")
            raise

    @classmethod
    def _create_queues(cls):
        config = cls._message_config.azure_service_bus_configuration
        queues = config.queues or []

        for queue_name in queues:
            if cls._check_queue_exists(queue_name):
                print(f"Queue '{queue_name}' already exists. Skipping creation.")
                continue

            cls._admin_client.create_queue(
                queue_name,
                max_size_in_megabytes=config.queue_max_size_in_megabytes,
                max_delivery_count=config.queue_max_delivery_count,
                default_message_time_to_live=config.queue_default_message_time_to_live, 
                lock_duration=timedelta(seconds=300),  # 5 minutes
            )
            print(f"Queue created: {queue_name}")

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
                print(f"Topic '{topic_name}' already exists. Skipping creation.")
            else:
                cls._admin_client.create_topic(
                    topic_name,
                    max_size_in_megabytes=config.topic_max_size_in_megabytes,
                    default_message_time_to_live=config.topic_default_message_time_to_live,
                )
                print(f"Topic created: {topic_name}")

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
            print(f"Subscription '{subscription_name}' for topic '{topic_name}' already exists. Skipping.")
            return

        cls._admin_client.create_subscription(
            topic_name,
            subscription_name,
            max_delivery_count=config.topic_subscription_max_delivery_count,
            default_message_time_to_live=config.topic_subscription_default_message_time_to_live,
            lock_duration=timedelta(seconds=300),
        )
        print(f"Subscription '{subscription_name}' created for topic '{topic_name}'")

    @classmethod
    def _check_subscription_exists(cls, topic_name: str, subscription_name: str) -> bool:
        try:
            cls._admin_client.get_subscription(topic_name, subscription_name)
            return True
        except Exception:
            return False
