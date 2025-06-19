from abc import ABC, abstractmethod

from blocks_genesis._message.consumer_message import ConsumerMessage


class MessageClient(ABC):
    @abstractmethod
    async def send_to_consumer_async(self, consumer_message: ConsumerMessage) -> None:
        pass

    @abstractmethod
    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage) -> None:
        pass
