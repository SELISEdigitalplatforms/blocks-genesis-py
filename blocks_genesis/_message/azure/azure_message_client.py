import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional

from asyncio import Lock
from pydantic import BaseModel
from collections import defaultdict
from datetime import datetime
from azure.servicebus.aio import ServiceBusClient, ServiceBusSender
from azure.servicebus import ServiceBusMessage

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._delegation.constants import DELEGATION_GRANT_HEADER
from blocks_genesis._delegation.grant_factory import get_delegation_grant_factory
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._message.consumer_message import ConsumerMessage
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_client import MessageClient
from blocks_genesis._message.message_configuration import MessageConfiguration

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
        self._message_config.connection = self._message_config.connection or get_blocks_secret().MessageConnectionString
        self._client = ServiceBusClient.from_connection_string(self._message_config.connection)
        self._senders: Dict[str, ServiceBusSender] = {}
        self._sender_locks: Dict[str, Lock] = defaultdict(Lock)
        self._initialize_senders()

    @classmethod
    def initialize(cls, message_config: MessageConfiguration):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(message_config)
                MessageClient.set_active_instance(cls._instance)
                logger.info("AzureMessageClient singleton initialized.")

    @classmethod
    def get_instance(cls) -> 'AzureMessageClient':
        if cls._instance is None:
            raise RuntimeError("AzureMessageClient not initialized. Call `initialize()` first.")
        return cls._instance

    def _initialize_senders(self):
        queues = self._message_config.azure_service_bus_configuration.queues or []
        topics = self._message_config.azure_service_bus_configuration.topics or []
        logger.info(f"Initializing Azure Service Bus senders for queues: {queues} and topics: {topics}")

        for name in queues + topics:
            self._senders[name] = self._build_sender(name)

    def _build_sender(self, name: str) -> ServiceBusSender:
        """A sender of the kind this destination was configured as.

        Anything not named as a queue is a topic, which is how an unconfigured name has
        always been treated.
        """
        queues = self._message_config.azure_service_bus_configuration.queues or []
        return (
            self._client.get_queue_sender(queue_name=name)
            if name in queues
            else self._client.get_topic_sender(topic_name=name)
        )

    async def _get_sender(self, name: str) -> ServiceBusSender:
        if name not in self._senders:
            self._senders[name] = self._build_sender(name)
        return self._senders[name]

    async def _replace_sender(self, name: str) -> ServiceBusSender:
        """Drop a sender whose link failed and put a fresh one in its place.

        Called with the destination's lock held, so nothing else is inside the sender
        while it is swapped. Closing the old one is best effort -- its handler is the
        thing that just failed.
        """
        stale = self._senders.pop(name, None)
        if stale is not None:
            try:
                await stale.close()
            except Exception:
                logger.debug("Ignoring close error on stale sender '%s'", name, exc_info=True)
        return await self._get_sender(name)


    async def _send_to_azure_bus_async(self, consumer_message: ConsumerMessage, is_topic: bool = False):
        security_context = BlocksContextManager.get_context()

        with Activity("messaging.azure.servicebus.send") as activity:
            activity.set_properties({
                "messaging.system": "azure.servicebus",
                "messaging.destination": consumer_message.consumer_name,
                "messaging.destination_kind": "topic" if is_topic else "queue",
                "messaging.operation": "send",
                "messaging.message_type": type(consumer_message.payload).__name__,
                "baggage.TenantId": BlocksContextManager.get_context().tenant_id
            })

            payload_dict = self._serialize_payload(consumer_message.payload)

            message_body = EventMessage(
                body=json.dumps(payload_dict),
                type=consumer_message.payload_type
            )

            application_properties = {
                "TenantId": security_context.tenant_id if security_context else None,
                "TraceId": activity.get_trace_id(),
                "SpanId": activity.get_span_id(),
                "SecurityContext": consumer_message.context or json.dumps(
                    BlocksContextManager.create_sanitized_for_transport(security_context),
                    cls=DateTimeEncoder,
                ),
                "Baggage": json.dumps(activity.get_all_root_attributes())
            }

            # Written while a validated user token is still in scope. SecurityContext above is
            # context and tracing only; this grant is what carries authority. No user, no grant,
            # no header.
            delegation_grant = await get_delegation_grant_factory().create_for_send_async(
                consumer_message.delegation_ttl_seconds
            )
            if delegation_grant:
                application_properties[DELEGATION_GRANT_HEADER] = delegation_grant

            sb_message = ServiceBusMessage(
                body=json.dumps(message_body.__dict__),
                application_properties=application_properties
            )

            name = consumer_message.consumer_name
            try:
                # One sender per destination is shared by every caller, and an async
                # ServiceBusSender cannot be driven by two coroutines at once. Its
                # `_open` sets `_running` only after several awaits, so a second caller
                # walks in and both rebuild the link, nulling `_handler` / `_session`
                # under each other -- surfacing as a bare AttributeError from inside the
                # SDK. Serialise per destination so only one send is ever in a sender.
                async with self._sender_locks[name]:
                    sender = await self._get_sender(name)
                    try:
                        await sender.send_messages(sb_message)
                    except Exception:
                        # The handler is torn down on failure; rebuild rather than hand
                        # the next caller a sender whose link just died.
                        logger.warning(
                            "Send to '%s' failed; rebuilding its sender and retrying once",
                            name,
                            exc_info=True,
                        )
                        sender = await self._replace_sender(name)
                        await sender.send_messages(sb_message)
                logger.info(
                    "Message published to Azure Service Bus %s '%s'",
                    "topic" if is_topic else "queue",
                    name,
                )
                return True
            except Exception as ex:
                logger.error(
                    "Failed to publish message to Azure Service Bus '%s': %s",
                    name,
                    str(ex),
                )
                raise

    def _serialize_payload(self, payload):
        if isinstance(payload, BaseModel):
            return payload.model_dump()
        elif is_dataclass(payload):
            return asdict(payload)
        elif isinstance(payload, dict):
            return payload
        elif isinstance(payload, str):
            return {"message": payload}
        else:
            raise TypeError(f"Unsupported payload type: {type(payload)}")

    async def send_to_consumer_async(self, consumer_message: ConsumerMessage) -> bool:
        return await self._send_to_azure_bus_async(consumer_message)

    async def send_to_mass_consumer_async(self, consumer_message: ConsumerMessage) -> bool:
        return await self._send_to_azure_bus_async(consumer_message, is_topic=True)

    async def close(self):
        await self._client.close()
