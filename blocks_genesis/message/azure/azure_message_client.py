import json
from typing import TypeVar, Generic, Dict, Optional
from azure.servicebus.aio import ServiceBusClient, ServiceBusSender
from azure.servicebus import ServiceBusMessage
from collections import defaultdict
from asyncio import Lock

from context import get_context  # Your equivalent of BlocksContext.GetContext
from tracing import tracer       # Assuming OpenTelemetry Tracer is configured
from message_config import MessageConfiguration
from consumer_message import ConsumerMessage
from message import Message

T = TypeVar('T')

class AzureMessageClient:
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
        security_context = get_context()

        with tracer.start_as_current_span("messaging.azure.servicebus.send") as span:
            span.set_attribute("messaging.system", "azure.servicebus")
            span.set_attribute("messaging.destination", consumer_message.consumer_name)
            span.set_attribute("messaging.destination_kind", "topic" if is_topic else "queue")
            span.set_attribute("messaging.operation", "send")
            span.set_attribute("messaging.message_type", type(consumer_message.payload).__name__)

            sender = await self._get_sender(consumer_message.consumer_name)

            message_body = Message(
                body=json.dumps(consumer_message.payload),
                type=type(consumer_message.payload).__name__
            )

            sb_message = ServiceBusMessage(
                body=json.dumps(message_body.__dict__),
                application_properties={
                    "TenantId": security_context.tenant_id if security_context else None,
                    "TraceId": span.get_span_context().trace_id,
                    "SpanId": span.get_span_context().span_id,
                    "SecurityContext": consumer_message.context or json.dumps(security_context.__dict__ if security_context else {}),
                    "Baggage": json.dumps(dict(span.get_span_context().baggage.items())) if span.get_span_context().baggage else "{}"
                }
            )

            await sender.send_messages(sb_message)

    async def send_to_consumer_async(self, consumer_message: ConsumerMessage[T]):
        await self._send_to_azure_bus_async(consumer_message)

    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage[T]):
        await self._send_to_azure_bus_async(consumer_message, is_topic=True)
