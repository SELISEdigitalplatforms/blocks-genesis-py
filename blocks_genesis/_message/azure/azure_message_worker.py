import asyncio
import collections
import json
import logging
import types
from typing import Dict, List, Optional
from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver
from azure.servicebus import ServiceBusReceivedMessage
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._message.consumer import Consumer
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_configuration import MessageConfiguration

logger = logging.getLogger(__name__)


class AzureMessageWorker:
    def __init__(self, message_config: MessageConfiguration):
        self._logger = logger
        self._message_config = message_config
        self._consumer = Consumer()
        self._service_bus_client: Optional[ServiceBusClient] = None
        self._receivers: List[ServiceBusReceiver] = []
        self._active_message_renewals: Dict[str, asyncio.Event] = {}
        self._tracer = trace.get_tracer(__name__)

    def initialize(self):
        connection = (
            self._message_config.connection
            or get_blocks_secret().MessageConnectionString
        )
        if not connection:
            self._logger.error("Connection string missing")
            raise ValueError("Connection string missing")
        self._service_bus_client = ServiceBusClient.from_connection_string(connection)
        self._logger.info("Service Bus Client initialized")

    async def stop(self):
        for message_id, event in self._active_message_renewals.items():
            event.set()
        self._active_message_renewals.clear()

        for receiver in self._receivers:
            try:
                await receiver.close()
            except Exception as ex:
                self._logger.error("Error closing receiver: %s", ex)
        self._receivers.clear()

        if self._service_bus_client:
            await self._service_bus_client.close()

        self._logger.info("Worker stopped")

    async def run(self):
        if not self._service_bus_client:
            raise ValueError("Service Bus Client is not initialized")

        receiver_tasks = []

        for queue_name in self._message_config.azure_service_bus_configuration.queues:
            receiver = self._service_bus_client.get_queue_receiver(
                queue_name=queue_name,
                prefetch_count=self._message_config.azure_service_bus_configuration.queue_prefetch_count,
            )
            self._receivers.append(receiver)
            receiver_tasks.append(self.safe_receiver_wrapper(receiver, queue_name))

        for topic_name in self._message_config.azure_service_bus_configuration.topics:
            subscription_name = self._message_config.subscription_name.get(
                topic_name, "default-subscription"
            )
            receiver = self._service_bus_client.get_subscription_receiver(
                topic_name=topic_name,
                subscription_name=subscription_name,
                prefetch_count=self._message_config.azure_service_bus_configuration.topic_prefetch_count,
            )
            self._receivers.append(receiver)
            receiver_tasks.append(self.safe_receiver_wrapper(receiver, topic_name))

        self._logger.info("Receivers started")
        await asyncio.gather(*receiver_tasks, return_exceptions=True)

    async def safe_receiver_wrapper(self, receiver: ServiceBusReceiver, name: str):
        try:
            await self.process_receiver(receiver)
        except Exception as ex:
            self._logger.error(
                "Receiver task for %s crashed but will be ignored: %s", name, ex
            )

    async def process_receiver(self, receiver: ServiceBusReceiver):
        async with receiver:
            async for message in receiver:
                try:
                    await self.message_handler(receiver, message)
                except Exception as ex:
                    self._logger.error(
                        "Error in message_handler, skipping message: %s", ex
                    )
                    try:
                        await receiver.abandon_message(message)
                    except Exception as abandon_ex:
                        self._logger.warning(
                            "Failed to abandon message: %s", abandon_ex
                        )

    def decode_app_properties(self, properties):
        if not properties:
            return {}
        return {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (
                v.decode("utf-8") if isinstance(v, bytes) else v
            )
            for k, v in properties.items()
        }

    async def message_handler(
        self, receiver: ServiceBusReceiver, message: ServiceBusReceivedMessage
    ):
        message_id = message.message_id
        self._logger.info("Received message: %s", message_id)

        app_props = self.decode_app_properties(message.application_properties)
        trace_id = app_props.get("TraceId", "")
        span_id = app_props.get("SpanId", "")
        tenant_id = app_props.get("TenantId", "")
        security_context_raw = app_props.get("SecurityContext", "")
        baggage_str = app_props.get("Baggage", "{}")

        if security_context_raw:
            try:
                sc = json.loads(security_context_raw)
                BlocksContextManager.set_context(BlocksContextManager.create(**sc))
            except Exception as ctx_err:
                self._logger.warning("Invalid security context: %s", ctx_err)

        cancellation_event = asyncio.Event()
        self._active_message_renewals[message_id] = cancellation_event
        renewal_task = asyncio.create_task(
            self.start_auto_renewal_task(message, receiver, cancellation_event)
        )
        
        try:
            context = (
                TraceContextTextMapPropagator().extract(
                    {"traceparent": f"00-{trace_id}-{span_id}-01"}
                )
                if trace_id and span_id
                else None
            )

            with self._tracer.start_as_current_span(
                "process.messaging.azure.service.bus",
                context=context,
                kind=SpanKind.CONSUMER,
            ) as span:
                span.set_attribute("messaging.system", "azure.servicebus")
                span.set_attribute("message.id", message_id)
                span.set_attribute("SecurityContext", security_context_raw)
                span.set_attribute("message.body", str(message))
                span.set_attribute("baggage.TenantId", tenant_id)

                try:
                    baggages = json.loads(baggage_str)
                    for key, value in baggages.items():
                        span.set_attribute(f"baggage.{key}", value)
                except json.JSONDecodeError:
                    self._logger.warning("Invalid baggage JSON")

                start_time = asyncio.get_event_loop().time()
                if message.body is None:
                    d = "{}"
                else:
                    body_bytes = (
                        b"".join(message.body)
                        if isinstance(
                            message.body,
                            (types.GeneratorType, collections.abc.Iterable),
                        )
                        else message.body
                    )
                    d = (
                        body_bytes.decode("utf-8")
                        if isinstance(body_bytes, (bytes, bytearray))
                        else str(body_bytes)
                    )

                msg = EventMessage(**json.loads(d))
                await self._consumer.process_message(msg.type, msg.body)
                processing_time = (asyncio.get_event_loop().time() - start_time) * 1000
                self._logger.info(
                    "Processed message %s in %.1fms", message_id, processing_time
                )

                span.set_attribute("response", "Successfully Completed")
                span.set_status(Status(StatusCode.OK, "Message processed successfully"))

                await receiver.complete_message(message)
                self._logger.info("Message %s completed", message_id)

        except Exception as ex:
            self._logger.error(
                "Processing failed for message %s: %s", message_id, ex
            )
            try:
                await receiver.abandon_message(message)
            except Exception as abandon_ex:
                self._logger.warning("Abandon message failed: %s", abandon_ex)
            raise
        finally:
            cancellation_event.set()
            self._active_message_renewals.pop(message_id, None)
            renewal_task.cancel()
            BlocksContextManager.clear_context()

    async def start_auto_renewal_task(
        self,
        message: ServiceBusReceivedMessage,
        receiver: ServiceBusReceiver,
        cancellation_event: asyncio.Event,
    ):
        message_id = message.message_id
        start_time = asyncio.get_event_loop().time()
        renewal_count = 0
        renewal_interval = (
            self._message_config.azure_service_bus_configuration.message_lock_renewal_interval_seconds
        )
        max_processing_time = (
            self._message_config.azure_service_bus_configuration.max_message_processing_time_in_minutes
            * 60
        )

        try:
            while not cancellation_event.is_set():
                try:
                    await asyncio.wait_for(
                        cancellation_event.wait(), timeout=renewal_interval
                    )
                except asyncio.TimeoutError:
                    pass  # normal

                if cancellation_event.is_set():
                    break

                processing_time = asyncio.get_event_loop().time() - start_time
                if processing_time > max_processing_time:
                    self._logger.warning(
                        "Message %s exceeded max time; stopping lock renewal",
                        message_id,
                    )
                    break

                try:
                    await receiver.renew_message_lock(message)
                    renewal_count += 1
                    self._logger.info(
                        "Renewed lock for message %s (%s)", message_id, renewal_count
                    )
                except Exception as ex:
                    self._logger.warning(
                        "Lock renewal failed for %s: %s", message_id, ex
                    )
                    cancellation_event.set()
                    
        except asyncio.CancelledError:
            self._logger.info("Auto-renewal cancelled for %s", message_id)
        except Exception as ex:
            self._logger.error("Auto-renewal error for %s: %s", message_id, ex)
        finally:
            self._logger.info(
                "Auto-renewal finished for %s after %s renewals",
                message_id,
                renewal_count,
            )