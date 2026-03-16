from typing import List, Optional
from datetime import timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class AzureServiceBusConfiguration(BaseModel):
    queues: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    queue_max_size_in_megabytes: int = 1024
    queue_max_delivery_count: int = 2
    queue_prefetch_count: int = 10
    queue_default_message_time_to_live: timedelta = timedelta(days=7)

    topic_prefetch_count: int = 10
    topic_max_size_in_megabytes: int = 1024
    topic_default_message_time_to_live: timedelta = timedelta(days=7)

    topic_subscription_max_delivery_count: int = 2
    topic_subscription_default_message_time_to_live: timedelta = timedelta(days=7)
    max_concurrent_calls: int = 5
    max_message_processing_time_in_minutes: int = 300
    message_lock_renewal_interval_seconds: int = 270

    def set_queues(self, queue_list: List[str]):
        self.queues = [q.lower() for q in queue_list if q and q.strip()]

    def set_topics(self, topic_list: List[str]):
        self.topics = [t.lower() for t in topic_list if t and t.strip()]


class ConsumerSubscription(BaseModel):
    queue_name: str
    exchange_name: str = ""
    prefetch_count: int = 5
    exchange_type: str = "fanout"
    routing_key: str = ""
    should_bypass_authorization: bool = False
    durable: bool = True
    parallel_processing: bool = False

    @classmethod
    def bind_to_queue(cls, queue_name: str, prefetch_count: int = 5) -> "ConsumerSubscription":
        """Creates a subscription that binds directly to a queue."""
        return cls(queue_name=queue_name, exchange_name="", prefetch_count=prefetch_count)

    @classmethod
    def bind_to_queue_via_exchange(
        cls,
        queue_name: str,
        exchange_name: str,
        prefetch_count: int = 5,
        parallel_processing: bool = False,
    ) -> "ConsumerSubscription":
        """Creates a subscription that binds a queue via an exchange."""
        return cls(
            queue_name=queue_name,
            exchange_name=exchange_name,
            prefetch_count=prefetch_count,
            parallel_processing=parallel_processing,
        )


class RabbitMqConfiguration(BaseModel):
    consumer_subscriptions: List[ConsumerSubscription] = Field(default_factory=list)
    message_ttl_seconds: int = 0


class MessageConfiguration(BaseModel):
    connection: Optional[str] = None
    service_name: Optional[str] = None
    queues: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    consumer_subscriptions: Optional[List[ConsumerSubscription]] = None
    azure_service_bus_configuration: Optional[AzureServiceBusConfiguration] = None
    rabbit_mq_configuration: Optional[RabbitMqConfiguration] = None

    def get_subscription_name(self, topic_name: str) -> str:
        return f"{topic_name}_sub_{self.service_name}"

    def resolve_provider(self) -> None:
        """
        Auto-detects the messaging provider from the connection string and populates
        the appropriate sub-configuration if neither is already set.
        amqp:// or amqps:// → RabbitMQ, otherwise → Azure Service Bus.
        Mirrors the .NET Constants.GetMessageConfiguration pattern.
        """
        if self.azure_service_bus_configuration or self.rabbit_mq_configuration:
            return

        if not self.connection:
            return

        provider = _get_provider(self.connection)

        if provider == "rabbitmq":
            self.rabbit_mq_configuration = RabbitMqConfiguration(
                consumer_subscriptions=[
                    ConsumerSubscription.bind_to_queue(q) for q in self.queues
                ] + (self.consumer_subscriptions or []),
            )
        else:
            self.azure_service_bus_configuration = AzureServiceBusConfiguration(
                queues=self.queues,
                topics=self.topics,
            )


def _get_provider(connection_string: str) -> str:
    """Detects the messaging provider from the connection string scheme."""
    try:
        parsed = urlparse(connection_string)
        if parsed.scheme.lower() in ("amqp", "amqps"):
            return "rabbitmq"
    except Exception:
        pass
    return "azure"
