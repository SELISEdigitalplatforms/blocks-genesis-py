import asyncio
from typing import Optional
from azure.servicebus.aio import ServiceBusAdministrationClient
from azure.servicebus.management import (
    CreateQueueOptions,
    CreateTopicOptions,
    CreateSubscriptionOptions
)

from blocks_genesis.message.message_configuration import MessageConfiguration



class ConfigAzureServiceBus:
    _admin_client: Optional[ServiceBusAdministrationClient] = None
    _message_config: Optional[MessageConfiguration] = None

    @classmethod
    async def configure_queue_and_topic_async(cls, message_config: MessageConfiguration):
        if not message_config.connection:
            print("Error in creation of message queues/topics")
            return

        try:
            cls._admin_client = ServiceBusAdministrationClient.from_connection_string(message_config.connection)
            cls._message_config = message_config

            await asyncio.gather(
                cls._create_queues_async(),
                cls._create_topics_async()
            )
        except Exception as ex:
            print(f"Exception during service bus configuration: {ex}")
            raise

    @classmethod
    async def _create_queues_async(cls):
        try:
            tasks = []
            queues = cls._message_config.azure_service_bus_config.queues if cls._message_config.azure_service_bus_config else []

            for queue_name in queues:
                if await cls._check_queue_exists_async(queue_name):
                    continue

                options = CreateQueueOptions(
                    name=queue_name,
                    max_size_in_megabytes=cls._message_config.azure_service_bus_config.queue_max_size_in_megabytes,
                    max_delivery_count=cls._message_config.azure_service_bus_config.queue_max_delivery_count,
                    default_message_time_to_live=cls._message_config.azure_service_bus_config.queue_default_message_ttl
                )
                tasks.append(cls._admin_client.create_queue(options))

            await asyncio.gather(*tasks)

        except Exception as ex:
            print(f"Exception while creating queues: {ex}")
            raise

    @classmethod
    async def _check_queue_exists_async(cls, queue_name: str) -> bool:
        return await cls._admin_client.get_queue_runtime_properties(queue_name) is not None

    @classmethod
    async def _create_topics_async(cls):
        try:
            tasks = []
            topics = cls._message_config.azure_service_bus_config.topics if cls._message_config.azure_service_bus_config else []

            for topic_name in topics:
                if await cls._check_topic_exists_async(topic_name):
                    continue

                options = CreateTopicOptions(
                    name=topic_name,
                    max_size_in_megabytes=cls._message_config.azure_service_bus_config.topic_max_size_in_megabytes,
                    default_message_time_to_live=cls._message_config.azure_service_bus_config.topic_default_message_ttl
                )
                tasks.append(cls._admin_client.create_topic(options))

            await asyncio.gather(*tasks)

            # Create subscriptions
            await asyncio.gather(*(cls._create_topic_subscription_async(tn) for tn in topics))

        except Exception as ex:
            print(f"Exception while creating topics: {ex}")
            raise

    @classmethod
    async def _check_topic_exists_async(cls, topic_name: str) -> bool:
        return await cls._admin_client.get_topic_runtime_properties(topic_name) is not None

    @classmethod
    async def _create_topic_subscription_async(cls, topic_name: str):
        try:
            subscription_name = cls._message_config.get_subscription_name(topic_name)
            if await cls._check_subscription_exists_async(topic_name, subscription_name):
                return

            options = CreateSubscriptionOptions(
                topic_name=topic_name,
                subscription_name=subscription_name,
                max_delivery_count=cls._message_config.azure_service_bus_config.topic_subscription_max_delivery_count,
                default_message_time_to_live=cls._message_config.azure_service_bus_config.topic_subscription_default_message_ttl
            )

            await cls._admin_client.create_subscription(options)

        except Exception as ex:
            print(f"Exception while creating subscription for topic {topic_name}: {ex}")
            raise

    @classmethod
    async def _check_subscription_exists_async(cls, topic_name: str, subscription_name: str) -> bool:
        return await cls._admin_client.get_subscription_runtime_properties(topic_name, subscription_name) is not None
