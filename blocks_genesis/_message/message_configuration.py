from typing import List, Optional
from datetime import timedelta

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



class MessageConfiguration(BaseModel):
    connection: Optional[str] = None
    service_name: Optional[str] = None
    azure_service_bus_configuration: Optional[AzureServiceBusConfiguration] = None

    def get_subscription_name(self, topic_name: str) -> str:
        return f"{topic_name}_sub_{self.service_name}"
