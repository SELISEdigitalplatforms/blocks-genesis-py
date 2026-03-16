from abc import ABC, abstractmethod
from typing import Optional

from blocks_genesis._message.consumer_message import ConsumerMessage


class MessageClient(ABC):
    _active_instance: Optional["MessageClient"] = None

    @abstractmethod
    async def send_to_consumer_async(self, consumer_message: ConsumerMessage) -> bool:
        pass

    @abstractmethod
    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage) -> bool:
        pass

    @classmethod
    def set_active_instance(cls, instance: "MessageClient") -> None:
        cls._active_instance = instance

    @classmethod
    def get_instance(cls) -> "MessageClient":
        if cls._active_instance is None:
            raise RuntimeError("No MessageClient has been initialized. Configure Azure or RabbitMQ first.")
        return cls._active_instance