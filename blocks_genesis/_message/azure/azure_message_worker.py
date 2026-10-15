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
from blocks_genesis._delegation.constants import DELEGATION_GRANT_HEADER
from blocks_genesis._delegation.context import DelegatedTokenContext
from blocks_genesis._delegation.grant_binding import (
    GRANT_MISMATCH_DEAD_LETTER_REASON,
    check_grant_binding,
)
from blocks_genesis._delegation.grant_store import get_delegation_grant_store
from blocks_genesis._delegation.token_provider import get_delegated_token_provider
from blocks_genesis._message.consumer import Consumer
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_configuration import MessageConfiguration
from blocks_genesis._message.security_context_header import (
    INVALID_SECURITY_CONTEXT_DEAD_LETTER_REASON,
    SECURITY_CONTEXT_INVALID,
    parse_security_context_header,
)

logger = logging.getLogger(__name__)

PROCESSING_FAILED_DEAD_LETTER_REASON = "ProcessingFailed"
UNDECODABLE_MESSAGE_DEAD_LETTER_REASON = "UndecodableMessage"


class _UndecodableMessage(Exception):
    """The message's own properties or body are not valid UTF-8 (not an error from the handler)."""


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
            receiver_tasks.append(
                self.safe_receiver_wrapper(
                    receiver,
                    queue_name,
                    max_delivery_count=self._message_config.azure_service_bus_configuration.queue_max_delivery_count,
                )
            )

        for topic_name in self._message_config.azure_service_bus_configuration.topics:
            subscription_name = self._message_config.get_subscription_name(topic_name)
            receiver = self._service_bus_client.get_subscription_receiver(
                topic_name=topic_name,
                subscription_name=subscription_name,
                prefetch_count=self._message_config.azure_service_bus_configuration.topic_prefetch_count,
            )
            self._receivers.append(receiver)
            receiver_tasks.append(
                self.safe_receiver_wrapper(
                    receiver,
                    topic_name,
                    max_delivery_count=self._message_config.azure_service_bus_configuration.topic_subscription_max_delivery_count,
                )
            )

        self._logger.info("Receivers started")
        await asyncio.gather(*receiver_tasks, return_exceptions=True)

    async def safe_receiver_wrapper(
        self, receiver: ServiceBusReceiver, name: str, max_delivery_count: Optional[int] = None
    ):
        try:
            await self.process_receiver(receiver, max_delivery_count=max_delivery_count)
        except Exception as ex:
            self._logger.error(
                "Receiver task for %s crashed but will be ignored: %s", name, ex
            )

    async def process_receiver(self, receiver: ServiceBusReceiver, max_delivery_count: Optional[int] = None):
        async with receiver:
            async for message in receiver:
                try:
                    # message_handler settles every message itself, so nothing is settled here.
                    await self.message_handler(receiver, message, max_delivery_count=max_delivery_count)
                except Exception as ex:
                    self._logger.error(
                        "Error in message_handler, skipping message: %s", ex
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
        self,
        receiver: ServiceBusReceiver,
        message: ServiceBusReceivedMessage,
        *,
        max_delivery_count: Optional[int] = None,
    ):
        """Handle one message and settle it exactly once.

        `max_delivery_count` is the entity's configured MaxDeliveryCount. A failure on the final
        attempt is dead-lettered (and its checked grant released); without it every failure is
        abandoned for a retry.
        """
        message_id = message.message_id
        self._logger.info("Received message: %s", message_id)

        cancellation_event = asyncio.Event()
        self._active_message_renewals[message_id] = cancellation_event
        renewal_task = asyncio.create_task(
            self.start_auto_renewal_task(message, receiver, cancellation_event)
        )

        delegation_grant = None
        grant_verified = False
        try:
            try:
                app_props = self.decode_app_properties(message.application_properties)
            except UnicodeDecodeError as ex:
                raise _UndecodableMessage() from ex
            trace_id = app_props.get("TraceId", "")
            span_id = app_props.get("SpanId", "")
            tenant_id = app_props.get("TenantId", "")
            security_context_raw = app_props.get("SecurityContext", "")
            baggage_str = app_props.get("Baggage", "{}")
            delegation_grant = app_props.get(DELEGATION_GRANT_HEADER) or None

            security_context_valid, security_context = parse_security_context_header(security_context_raw)
            if security_context_valid:
                BlocksContextManager.set_context(security_context)

            # Deliberately not a span attribute and not in baggage: the grant id is a credential.
            DelegatedTokenContext.set(delegation_grant)

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
                span.set_attribute("usage", True)

                try:
                    baggages = json.loads(baggage_str)
                    if isinstance(baggages, dict):
                        for key, value in baggages.items():
                            span.set_attribute(f"baggage.{key}", value)
                    else:
                        self._logger.warning("Baggage header is not a JSON object; ignored")
                except (json.JSONDecodeError, TypeError):
                    self._logger.warning("Invalid baggage JSON")

                if not security_context_valid:
                    # Final, so not abandoned: a redelivery would be refused again. Neither the
                    # header nor the parse error is logged: either can carry personal data.
                    self._logger.error("Message %s not handled: %s", message_id, SECURITY_CONTEXT_INVALID)
                    span.set_status(Status(StatusCode.ERROR, SECURITY_CONTEXT_INVALID))
                    await receiver.dead_letter_message(
                        message,
                        reason=INVALID_SECURITY_CONTEXT_DEAD_LETTER_REASON,
                        error_description=SECURITY_CONTEXT_INVALID,
                    )
                    return

                refusal = await check_grant_binding(
                    BlocksContextManager.get_context(),
                    delegation_grant,
                    get_delegation_grant_store(),
                    message_id,
                )
                if refusal:
                    # Final, so not abandoned: a redelivery would be refused again. The grant is
                    # kept; its TTL removes it.
                    span.set_status(Status(StatusCode.ERROR, refusal))
                    await receiver.dead_letter_message(
                        message,
                        reason=GRANT_MISMATCH_DEAD_LETTER_REASON,
                        error_description=refusal,
                    )
                    return

                grant_verified = True
                start_time = asyncio.get_event_loop().time()
                try:
                    d = self._decode_message_body(message)
                except UnicodeDecodeError as ex:
                    raise _UndecodableMessage() from ex

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

                # Order is fixed: business op -> ACK -> DEL. A grant released before the settle
                # would leave a redelivery unable to mint a token.
                await self._release_delegation_grant(delegation_grant)

        except _UndecodableMessage:
            self._logger.error("Message %s could not be decoded; dead-lettering it", message_id)
            await self._settle_undecodable(receiver, message, delegation_grant if grant_verified else None)
        except Exception as ex:
            self._logger.error(
                "Processing failed for message %s: %s", message_id, ex
            )
            await self._settle_failure(
                receiver, message, ex, max_delivery_count, delegation_grant if grant_verified else None
            )
        finally:
            cancellation_event.set()
            self._active_message_renewals.pop(message_id, None)
            renewal_task.cancel()
            BlocksContextManager.clear_context()
            DelegatedTokenContext.clear()

    async def _settle_undecodable(
        self,
        receiver: ServiceBusReceiver,
        message: ServiceBusReceivedMessage,
        verified_grant: Optional[str],
    ) -> None:
        """Dead-letter at once: a redelivery cannot decode either. Business op -> DLQ -> DEL."""
        try:
            await receiver.dead_letter_message(
                message,
                reason=UNDECODABLE_MESSAGE_DEAD_LETTER_REASON,
                error_description="UnicodeDecodeError",
            )
        except Exception as dead_letter_ex:
            self._logger.warning("Dead-letter message failed: %s", dead_letter_ex)
            return
        await self._release_delegation_grant(verified_grant)

    async def _settle_failure(
        self,
        receiver: ServiceBusReceiver,
        message: ServiceBusReceivedMessage,
        ex: Exception,
        max_delivery_count: Optional[int],
        verified_grant: Optional[str],
    ) -> None:
        """Abandon for a retry, or dead-letter on the final attempt and release the checked grant.

        `delivery_count` counts the previous failed deliveries, so the first delivery is attempt 1.
        A grant that was never checked is kept: it may not belong to this message.
        """
        if max_delivery_count is None or (message.delivery_count or 0) + 1 < max_delivery_count:
            try:
                await receiver.abandon_message(message)
            except Exception as abandon_ex:
                self._logger.warning("Abandon message failed: %s", abandon_ex)
            return

        attempt = (message.delivery_count or 0) + 1
        try:
            await receiver.dead_letter_message(
                message,
                reason=PROCESSING_FAILED_DEAD_LETTER_REASON,
                error_description=f"{type(ex).__name__}; attempt {attempt} of {max_delivery_count}",
            )
        except Exception as dead_letter_ex:
            self._logger.warning("Dead-letter message failed: %s", dead_letter_ex)
            return
        # Order is fixed: business op -> DLQ -> DEL.
        await self._release_delegation_grant(verified_grant)

    async def _release_delegation_grant(self, delegation_grant: Optional[str]) -> None:
        """Remove the grant and its cached token after a final settle.

        Best effort: a failure is logged and never raised, so it cannot cause a second settle. The
        absolute TTL still bounds the grant. A message that was abandoned instead keeps its grant,
        so the redelivery can still mint a token.
        """
        if not delegation_grant:
            return

        try:
            get_delegated_token_provider().invalidate(delegation_grant)
            await get_delegation_grant_store().delete_async(delegation_grant)
        except Exception as ex:
            # Not the grant id: it is a credential.
            self._logger.warning(
                "Could not release the delegation grant (%s); its TTL removes it.", type(ex).__name__
            )

    def _decode_message_body(self, message: ServiceBusReceivedMessage) -> str:
        """Decode the Service Bus message body to a UTF-8 string (empty JSON if absent)."""
        if message.body is None:
            return "{}"
        body_bytes = (
            b"".join(message.body)
            if isinstance(
                message.body,
                (types.GeneratorType, collections.abc.Iterable),
            )
            else message.body
        )
        if isinstance(body_bytes, (bytes, bytearray)):
            return body_bytes.decode("utf-8")
        return str(body_bytes)

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