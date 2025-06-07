from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from blocks_genesis.message.consumer_message import ConsumerMessage


T = TypeVar('T')

class MessageClient(ABC):
    @abstractmethod
    async def send_to_consumer_async(self, consumer_message: ConsumerMessage[T]) -> None:
        pass

    @abstractmethod
    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage[T]) -> None:
        pass
