import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional

from asyncio import Lock
from collections import defaultdict
from datetime import datetime
from azure.servicebus.aio import ServiceBusClient, ServiceBusSender
from azure.servicebus import ServiceBusMessage

from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.lmt.activity import Activity
from blocks_genesis.message.consumer_message import ConsumerMessage
from blocks_genesis.message.event_message import EventMessage
from blocks_genesis.message.message_client import MessageClient
from blocks_genesis.message.message_configuration import MessageConfiguration

logger = logging.getLogger(__name__)

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class AzureMessageClient(MessageClient):
    _instance: Optional['AzureMessageClient'] = None
    _singleton_lock = threading.Lock()

    def __init__(self, message_config: MessageConfiguration):
        self._message_config = message_config
        self._client = ServiceBusClient.from_connection_string(message_config.connection)
        self._senders: Dict[str, ServiceBusSender] = {}
        self._sender_locks: Dict[str, Lock] = defaultdict(Lock)
        self._initialize_senders()

    @classmethod
    def initialize(cls, message_config: MessageConfiguration):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(message_config)
                logger.info("✅ AzureMessageClient singleton initialized.")

    @classmethod
    def get_instance(cls) -> 'AzureMessageClient':
        if cls._instance is None:
            raise Exception("AzureMessageClient not initialized. Call `initialize()` first.")
        return cls._instance

    def _initialize_senders(self):
        queues = self._message_config.azure_service_bus_configuration.queues or []
        topics = self._message_config.azure_service_bus_configuration.topics or []
        logger.info(f"Initializing Azure Service Bus senders for queues: {queues} and topics: {topics}")

        for name in queues + topics:
            self._senders[name] = (
                self._client.get_queue_sender(queue_name=name)
                if name in queues
                else self._client.get_topic_sender(topic_name=name)
            )

    async def _get_sender(self, name: str) -> ServiceBusSender:
        if name in self._senders:
            return self._senders[name]

        async with self._sender_locks[name]:
            if name not in self._senders:
                self._senders[name] = self._client.get_topic_sender(topic_name=name)
            return self._senders[name]

    async def _send_to_azure_bus_async(self, consumer_message: ConsumerMessage, is_topic: bool = False):
        security_context = BlocksContextManager.get_context()

        with Activity("messaging.azure.servicebus.send") as activity:
            activity.set_properties({
                "messaging.system": "azure.servicebus",
                "messaging.destination": consumer_message.consumer_name,
                "messaging.destination_kind": "topic" if is_topic else "queue",
                "messaging.operation": "send",
                "messaging.message_type": type(consumer_message.payload).__name__
            })

            sender = await self._get_sender(consumer_message.consumer_name)

            payload_dict = self._serialize_payload(consumer_message.payload)

            message_body = EventMessage(
                body=json.dumps(payload_dict),
                type=type(consumer_message.payload).__name__
            )

            sb_message = ServiceBusMessage(
                body=json.dumps(message_body.__dict__),
                application_properties={
                    "TenantId": security_context.tenant_id if security_context else None,
                    "TraceId": Activity.get_trace_id(),
                    "SpanId": Activity.get_span_id(),
                    "SecurityContext": consumer_message.context or json.dumps(
                        security_context.__dict__ if security_context else {}, 
                        cls=DateTimeEncoder
                    ),
                    "Baggage": json.dumps(dict(Activity.get_baggages()))
                }
            )

            await sender.send_messages(sb_message)

    def _serialize_payload(self, payload):
        if is_dataclass(payload):
            return asdict(payload)
        elif isinstance(payload, dict):
            return payload
        elif isinstance(payload, str):
            return {"message": payload}
        else:
            raise TypeError(f"Unsupported payload type: {type(payload)}")

    async def send_to_consumer_async(self, consumer_message: ConsumerMessage):
        await self._send_to_azure_bus_async(consumer_message)

    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage):
        await self._send_to_azure_bus_async(consumer_message, is_topic=True)

    async def close(self):
        await self._client.close()
