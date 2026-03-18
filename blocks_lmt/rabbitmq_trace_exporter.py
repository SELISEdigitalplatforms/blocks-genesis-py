import threading
import asyncio
import json
from datetime import datetime, timedelta, timezone
from queue import Queue, Empty
from typing import Dict, List, Optional
from collections import deque, defaultdict
from dataclasses import asdict
import uuid

import aio_pika

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from blocks_lmt.azure_servicebus_trace_exporter import TraceData, FailedTraceBatch


class LmtRabbitMqTraceSender:
    """
    Handles sending traces to RabbitMQ with retry logic.
    Mirrors the C# LmtRabbitMqSender trace functionality.
    """

    TRACES_ROUTING_KEY = "traces"

    def __init__(
        self,
        service_name: str,
        connection_string: str,
        max_retries: int = 3,
        max_failed_batches: int = 100
    ):
        self._service_name = service_name
        self._connection_string = connection_string
        self._max_retries = max_retries
        self._max_failed_batches = max_failed_batches

        self._failed_trace_batches: deque[FailedTraceBatch] = deque()
        self._retry_lock = asyncio.Lock()

        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractChannel] = None
        self._exchange: Optional[aio_pika.abc.AbstractExchange] = None
        self._publish_lock = asyncio.Lock()

        self._exchange_name = self._get_exchange_name(service_name)

        # Start retry timer (runs every 30 seconds)
        self._stop_event = threading.Event()
        self._retry_timer = None
        self._start_retry_timer()

    @staticmethod
    def _get_exchange_name(service_name: str) -> str:
        """Get the exchange name for the service (lmt-{service_name})."""
        return f"lmt-{service_name}"

    async def _ensure_channel(self):
        """Ensure the RabbitMQ connection, channel, and exchange are initialized."""
        if self._channel is None or self._channel.is_closed:
            self._connection = await aio_pika.connect_robust(
                self._connection_string,
                client_properties={"connection_name": f"seliseblocks-lmt-client-{self._service_name}"},
            )
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                self._exchange_name,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
                auto_delete=False,
            )

    async def send_traces_async(
        self,
        tenant_batches: Dict[str, List[TraceData]],
        retry_count: int = 0
    ):
        """Send traces to RabbitMQ with retry logic."""
        current_retry = 0

        while current_retry <= self._max_retries:
            try:
                await self._ensure_channel()

                serializable_batches = {
                    tenant_id: [asdict(trace) for trace in traces]
                    for tenant_id, traces in tenant_batches.items()
                }

                payload = {
                    "Type": "traces",
                    "ServiceName": self._service_name,
                    "Data": serializable_batches
                }

                json_payload = json.dumps(payload, default=str)
                timestamp = datetime.now(timezone.utc)
                message_id = f"traces_{self._service_name}_{timestamp.strftime('%Y%m%d%H%M%S%f')[:-3]}_{uuid.uuid4().hex}"

                message = aio_pika.Message(
                    body=json_payload.encode("utf-8"),
                    content_type="application/json",
                    message_id=message_id,
                    correlation_id="blocks-lmt-service-traces",
                    type="traces",
                    headers={
                        "serviceName": self._service_name,
                        "timestamp": timestamp.isoformat(),
                        "source": "TracesSender",
                        "type": "traces",
                    },
                )

                async with self._publish_lock:
                    await self._exchange.publish(
                        message,
                        routing_key=self.TRACES_ROUTING_KEY,
                        mandatory=True,
                    )
                return

            except Exception as ex:
                print(f"Exception sending traces to RabbitMQ: {ex}, Retry: {current_retry}/{self._max_retries}")

            current_retry += 1

            if current_retry <= self._max_retries:
                delay = 2 ** (current_retry - 1)
                await asyncio.sleep(delay)

        # Queue for later retry if all retries failed
        if len(self._failed_trace_batches) < self._max_failed_batches:
            failed_batch = FailedTraceBatch(
                TenantBatches=tenant_batches,
                RetryCount=retry_count + 1,
                NextRetryTime=datetime.now(timezone.utc) + timedelta(minutes=2 ** retry_count)
            )
            self._failed_trace_batches.append(failed_batch)
            print(f"Queued trace batch for later retry. Failed batches in queue: {len(self._failed_trace_batches)}")
        else:
            print(f"Failed trace batch queue is full ({self._max_failed_batches}). Dropping batch.")

    def _start_retry_timer(self):
        """Start background thread for retrying failed batches every 30 seconds."""
        def retry_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            while not self._stop_event.is_set():
                try:
                    loop.run_until_complete(self._retry_failed_batches_async())
                except Exception as ex:
                    print(f"Error in retry worker: {ex}")

                self._stop_event.wait(30)

            loop.close()

        self._retry_timer = threading.Thread(target=retry_worker, daemon=True)
        self._retry_timer.start()

    async def _retry_failed_batches_async(self):
        """Retry failed trace batches that are ready for retry."""
        async with self._retry_lock:
            now = datetime.now(timezone.utc)
            batches_to_retry = []
            batches_to_requeue = []

            while self._failed_trace_batches:
                failed_batch = self._failed_trace_batches.popleft()
                if failed_batch.NextRetryTime <= now:
                    batches_to_retry.append(failed_batch)
                else:
                    batches_to_requeue.append(failed_batch)

            for batch in batches_to_requeue:
                self._failed_trace_batches.append(batch)

            for failed_batch in batches_to_retry:
                if failed_batch.RetryCount >= self._max_retries:
                    print(f"Trace batch exceeded max retries ({self._max_retries}). Dropping batch.")
                    continue

                print(f"Retrying failed trace batch (Attempt {failed_batch.RetryCount + 1}/{self._max_retries})")
                await self.send_traces_async(failed_batch.TenantBatches, failed_batch.RetryCount)

    async def close(self):
        """Close the RabbitMQ connection and flush remaining batches."""
        self._stop_event.set()
        if self._retry_timer:
            self._retry_timer.join(timeout=5)

        await self._retry_failed_batches_async()

        if self._channel and not self._channel.is_closed:
            await self._channel.close()
        if self._connection and not self._connection.is_closed:
            await self._connection.close()


class RabbitMqTraceExporter(SpanExporter):
    """
    OpenTelemetry SpanExporter that sends traces to RabbitMQ.
    Mirrors AzureServiceBusTraceExporter but uses RabbitMQ transport.
    """

    def __init__(
        self,
        x_blocks_key: str,
        service_name: str,
        connection_string: str,
        batch_size: int = 1000,
        flush_interval: float = 5.0,
        max_retries: int = 3,
        max_failed_batches: int = 100
    ):
        self._service_name = service_name
        self._x_blocks_key = x_blocks_key
        self._batch_size = batch_size
        self._flush_interval = flush_interval

        self._sender = LmtRabbitMqTraceSender(
            service_name=service_name,
            connection_string=connection_string,
            max_retries=max_retries,
            max_failed_batches=max_failed_batches
        )

        self._queue = Queue()
        self._stop_event = threading.Event()
        self._semaphore = threading.Semaphore(1)

        self._worker_thread = threading.Thread(target=self._run, daemon=True)
        self._worker_thread.start()

    def _extract_baggage_from_span(self, span) -> Dict[str, str]:
        """Extract baggage items from span attributes."""
        baggage_items = {}

        if hasattr(span, 'attributes') and span.attributes:
            for key, value in span.attributes.items():
                if key.startswith("baggage."):
                    baggage_items[key[8:]] = str(value)

        if "TenantId" not in baggage_items:
            baggage_items["TenantId"] = self._x_blocks_key

        return baggage_items

    def export(self, spans) -> SpanExportResult:
        """Export spans to RabbitMQ. Called by OpenTelemetry SDK."""
        try:
            for span in spans:
                baggage_items = self._extract_baggage_from_span(span)
                tenant_id = baggage_items.get("TenantId", self._x_blocks_key)

                trace_data = self._build_trace_data(span, baggage_items, tenant_id)
                self._queue.put(trace_data)

            return SpanExportResult.SUCCESS
        except Exception as ex:
            print(f"[RabbitMqTraceExporter] Export failed: {ex}")
            return SpanExportResult.FAILURE

    def _build_trace_data(self, span, baggage_items: Dict[str, str], tenant_id: str) -> TraceData:
        """Build TraceData object from OpenTelemetry span."""
        if span.parent:
            parent_span_id = format(span.parent.span_id, "016x")
            parent_id = f"00-{format(span.context.trace_id, '032x')}-{parent_span_id}-01"
        else:
            parent_span_id = "0000000000000000"
            parent_id = ""

        attributes = {}
        if hasattr(span, 'attributes') and span.attributes:
            attributes = {
                k: v for k, v in span.attributes.items()
                if not k.startswith("baggage.")
            }

        kind = str(span.kind) if hasattr(span, 'kind') else "INTERNAL"

        start_time = datetime.fromtimestamp(span.start_time / 1_000_000_000)
        end_time = datetime.fromtimestamp(span.end_time / 1_000_000_000)
        duration_ms = (span.end_time - span.start_time) / 1_000_000

        return TraceData(
            Timestamp=end_time.isoformat(),
            TraceId=format(span.context.trace_id, "032x"),
            SpanId=format(span.context.span_id, "016x"),
            ParentSpanId=parent_span_id,
            ParentId=parent_id,
            Kind=kind,
            ActivitySourceName=span.instrumentation_scope.name if hasattr(span, 'instrumentation_scope') else "",
            OperationName=span.name,
            StartTime=start_time.isoformat(),
            EndTime=end_time.isoformat(),
            Duration=duration_ms,
            Attributes=attributes,
            Status=str(span.status.status_code) if hasattr(span, 'status') else "UNSET",
            StatusDescription=span.status.description if hasattr(span, 'status') and span.status.description else "",
            Baggage=baggage_items,
            ServiceName=self._service_name,
            TenantId=tenant_id
        )

    def _run(self):
        """Background worker that batches traces by tenant and sends them."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        tenant_batches: Dict[str, List[TraceData]] = defaultdict(list)
        total_traces = 0

        while not self._stop_event.is_set():
            try:
                trace_data = self._queue.get(timeout=self._flush_interval)
                tenant_batches[trace_data.TenantId].append(trace_data)
                total_traces += 1

                while total_traces < self._batch_size:
                    try:
                        trace_data = self._queue.get_nowait()
                        tenant_batches[trace_data.TenantId].append(trace_data)
                        total_traces += 1
                    except Empty:
                        break

            except Empty:
                pass

            if tenant_batches:
                with self._semaphore:
                    try:
                        batches_to_send = dict(tenant_batches)
                        loop.run_until_complete(self._sender.send_traces_async(batches_to_send))
                    except Exception as e:
                        print(f"[RabbitMqTraceExporter] Send error: {e}")
                    finally:
                        tenant_batches.clear()
                        total_traces = 0

        # Flush remaining traces on shutdown
        if tenant_batches:
            with self._semaphore:
                try:
                    batches_to_send = dict(tenant_batches)
                    loop.run_until_complete(self._sender.send_traces_async(batches_to_send))
                except Exception as e:
                    print(f"[RabbitMqTraceExporter] Send error on shutdown: {e}")

        loop.run_until_complete(self._sender.close())
        loop.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush all pending spans."""
        import time
        deadline = time.time() + (timeout_millis / 1000.0)

        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.1)

        return True

    def shutdown(self):
        """Shutdown the exporter and flush remaining spans."""
        self._stop_event.set()
        self._worker_thread.join(timeout=self._flush_interval + 2)
