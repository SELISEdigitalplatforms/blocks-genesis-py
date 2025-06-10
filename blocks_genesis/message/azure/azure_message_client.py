from asyncio import Lock
import json
from typing import TypeVar, Dict
from azure.servicebus.aio import ServiceBusClient, ServiceBusSender
from azure.servicebus import ServiceBusMessage
from collections import defaultdict
from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.lmt.activity import Activity
from blocks_genesis.message.message_client import MessageClient
from blocks_genesis.message.message_configuration import MessageConfiguration
from consumer_message import ConsumerMessage
from blocks_genesis.message.event_message import Message

T = TypeVar('T')

class AzureMessageClient(MessageClient):
    """AzureMessageClient is responsible for sending messages to Azure Service Bus queues or topics.
    It initializes the ServiceBusClient and manages senders for each queue or topic.
    It uses asynchronous methods to send messages, ensuring that the client can handle multiple requests concurrently.
    """
    def __init__(self, message_config: MessageConfiguration):
        self._message_config = message_config
        self._client = ServiceBusClient.from_connection_string(message_config.connection)
        self._senders: Dict[str, ServiceBusSender] = {}
        self._sender_locks: Dict[str, Lock] = defaultdict(Lock)
        self._initialize_senders()

    def _initialize_senders(self):
        queues = self._message_config.azure_service_bus_config.queues if self._message_config.azure_service_bus_config else []
        topics = self._message_config.azure_service_bus_config.topics if self._message_config.azure_service_bus_config else []

        for name in queues + topics:
            self._senders[name] = self._client.get_queue_sender(queue_name=name) if name in queues else self._client.get_topic_sender(topic_name=name)

    async def _get_sender(self, name: str) -> ServiceBusSender:
        if name in self._senders:
            return self._senders[name]

        async with self._sender_locks[name]:
            if name not in self._senders:
                # Assume topic sender if not previously declared
                self._senders[name] = self._client.get_topic_sender(topic_name=name)
            return self._senders[name]

    async def _send_to_azure_bus_async(self, consumer_message: ConsumerMessage[T], is_topic: bool = False):
        security_context = BlocksContextManager.get_context()

        with Activity("messaging.azure.servicebus.send") as activity:
            activity.set_properties({"messaging.system": "azure.servicebus",
                                     "messaging.destination": consumer_message.consumer_name,
                                     "messaging.destination_kind": "topic" if is_topic else "queue",
                                     "messaging.operation": "send",
                                     "messaging.message_type": type(consumer_message.payload).__name__})

            sender = await self._get_sender(consumer_message.consumer_name)

            message_body = Message(
                body=json.dumps(consumer_message.payload),
                type=type(consumer_message.payload).__name__
            )

            sb_message = ServiceBusMessage(
                body=json.dumps(message_body.__dict__),
                application_properties={
                    "TenantId": security_context.tenant_id if security_context else None,
                    "TraceId": Activity.get_trace_id(),
                    "SpanId": Activity.get_span_id(),
                    "SecurityContext": consumer_message.context or json.dumps(security_context.__dict__ if security_context else {}),
                    "Baggage": json.dumps(dict(Activity.get_baggages()))
                }
            )

            await sender.send_messages(sb_message)

    async def send_to_consumer_async(self, consumer_message: ConsumerMessage[T]):
        await self._send_to_azure_bus_async(consumer_message)

    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage[T]):
        await self._send_to_azure_bus_async(consumer_message, is_topic=True)
