import asyncio
import json
import logging
from typing import Dict, List, Optional
from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver
from azure.servicebus import ServiceBusReceivedMessage
from opentelemetry import trace, baggage
from opentelemetry.trace import Status, StatusCode, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from blocks_genesis._auth.blocks_context import BlocksContext
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
        connection = self._message_config.connection or get_blocks_secret().MessageConnectionString
        if not connection:
            self._logger.error("Connection string missing")
            raise ValueError("Connection string missing")
        self._service_bus_client = ServiceBusClient.from_connection_string(connection)
        self._logger.info("✅ Service Bus Client initialized")

    async def stop(self):
        for message_id, event in self._active_message_renewals.items():
            event.set()
        self._active_message_renewals.clear()

        for receiver in self._receivers:
            try:
                await receiver.close()
            except Exception as ex:
                self._logger.error(f"Error closing receiver: {ex}")
        self._receivers.clear()

        if self._service_bus_client:
            await self._service_bus_client.close()

        self._logger.info("🛑 Worker stopped")

    async def run(self):
        if not self._service_bus_client:
            raise ValueError("Service Bus Client is not initialized")

        receiver_tasks = []

        # Process queues
        for queue_name in self._message_config.azure_service_bus_configuration.queues:
            receiver = self._service_bus_client.get_queue_receiver(
                queue_name=queue_name,
                prefetch_count=self._message_config.azure_service_bus_configuration.queue_prefetch_count
            )
            await receiver.__aenter__()  # Explicitly enter async context
            self._receivers.append(receiver)
            receiver_tasks.append(self.process_receiver(receiver))

        # Process topics
        for topic_name in self._message_config.azure_service_bus_configuration.topics:
            subscription_name = self._message_config.subscription_name.get(topic_name, "default-subscription")
            receiver = self._service_bus_client.get_subscription_receiver(
                topic_name=topic_name,
                subscription_name=subscription_name,
                prefetch_count=self._message_config.azure_service_bus_configuration.topic_prefetch_count
            )
            await receiver.__aenter__()
            self._receivers.append(receiver)
            receiver_tasks.append(self.process_receiver(receiver))

        self._logger.info("🚀 Receivers started")

        # This will block and keep running until cancelled
        await asyncio.gather(*receiver_tasks)

    async def process_receiver(self, receiver: ServiceBusReceiver):
        try:
            async for message in receiver:
                await self.message_handler(receiver, message)
        except asyncio.CancelledError:
            self._logger.info("Receiver cancelled")
        except Exception as ex:
            self._logger.error(f"Receiver error: {ex}")

    async def message_handler(self, receiver: ServiceBusReceiver, message: ServiceBusReceivedMessage):
        message_id = message.message_id
        self._logger.info(f"Received message: {message_id}")

        trace_id = message.application_properties.get(b"TraceId", b"").decode("utf-8")
        span_id = message.application_properties.get(b"SpanId", b"").decode("utf-8")
        tenant_id = message.application_properties.get(b"TenantId", b"").decode("utf-8")
        security_context_raw = message.application_properties.get(b"SecurityContext", b"").decode("utf-8")
        baggage_str = message.application_properties.get(b"Baggage", b"{}").decode("utf-8")

        if security_context_raw:
            BlocksContext.set_context(json.loads(security_context_raw))

        cancellation_event = asyncio.Event()
        self._active_message_renewals[message_id] = cancellation_event
        asyncio.create_task(self.start_auto_renewal_task(message, receiver, cancellation_event))

        try:
            context = TraceContextTextMapPropagator().extract({
                "traceparent": f"00-{trace_id}-{span_id}-01"
            })

            with self._tracer.start_as_current_span(
                "process.messaging.azure.service.bus",
                context=context,
                kind=SpanKind.CONSUMER
            ) as span:
                span.set_attribute("messaging.system", "azure.servicebus")
                span.set_attribute("message.id", message_id)
                span.set_attribute("SecurityContext", security_context_raw)
                span.set_attribute("message.body", str(message))
                baggages = json.loads(baggage_str)
                for key, value in baggages.items():
                    span.set_attribute(f"baggage.{key}", value)

                try:
                    start_time = asyncio.get_event_loop().time()
                    msg = EventMessage.parse_raw(message.body.decode("utf-8"))
                    await self._consumer.process_message(msg.type, msg.body)
                    processing_time = (asyncio.get_event_loop().time() - start_time) * 1000
                    self._logger.info(f"Processed message {message_id} in {processing_time:.1f}ms")

                    span.set_attribute("response", "Successfully Completed")
                    span.set_status(Status(StatusCode.OK, "Message processed successfully"))
                except Exception as ex:
                    self._logger.error(f"Error processing message {message_id}: {ex}")
                    span.set_status(Status(StatusCode.ERROR, str(ex)))
                    span.set_attribute("error", str(ex))
                    raise
                finally:
                    cancellation_event.set()
                    self._active_message_renewals.pop(message_id, None)
                    await receiver.complete_message(message)
                    self._logger.info(f"Message {message_id} completed.")
        except Exception as ex:
            self._logger.error(f"Unhandled error for message {message_id}: {ex}")
            cancellation_event.set()
            self._active_message_renewals.pop(message_id, None)
            await receiver.dead_letter_message(message, reason=str(ex))
        finally:
            BlocksContext.clear_context()

    async def start_auto_renewal_task(self, message: ServiceBusReceivedMessage, receiver: ServiceBusReceiver, cancellation_event: asyncio.Event):
        message_id = message.message_id
        start_time = asyncio.get_event_loop().time()
        renewal_count = 0
        renewal_interval = self._message_config.azure_service_bus_configuration.message_lock_renewal_interval_seconds
        max_processing_time = self._message_config.azure_service_bus_configuration.max_message_processing_time_minutes * 60

        try:
            while not cancellation_event.is_set():
                await asyncio.sleep(renewal_interval)
                processing_time = asyncio.get_event_loop().time() - start_time
                if processing_time > max_processing_time:
                    self._logger.warning(f"Message {message_id} exceeded max time ({max_processing_time}s); stopping lock renewal.")
                    break

                try:
                    await receiver.renew_message_lock(message)
                    renewal_count += 1
                    self._logger.info(f"Renewed lock for message {message_id} (#{renewal_count})")
                except Exception as ex:
                    self._logger.warning(f"Lock renewal failed for message {message_id}: {ex}")
                    break

            self._logger.info(f"Auto-renewal finished for {message_id} after {renewal_count} renewals")
        except asyncio.CancelledError:
            self._logger.info(f"Auto-renewal cancelled for {message_id} after {renewal_count} renewals")
        except Exception as ex:
            self._logger.error(f"Auto-renewal error for message {message_id}: {ex}")
