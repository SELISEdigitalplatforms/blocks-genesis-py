import logging
import threading
import asyncio
import json
from datetime import datetime, timedelta, timezone
from queue import Queue, Empty
from typing import Dict, List, Optional
from collections import deque
from dataclasses import asdict
import uuid

import aio_pika

from blocks_lmt.azure_servicebus_log_exporter import LogData, FailedLogBatch
from blocks_lmt.activity import Activity


class LmtRabbitMqSender:
    """
    Handles sending logs to RabbitMQ with retry logic.
    Mirrors the C# LmtRabbitMqSender implementation.
    """

    LOGS_ROUTING_KEY = "logs"

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

        self._failed_log_batches: deque[FailedLogBatch] = deque()
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

    async def send_logs_async(self, logs: List[LogData], retry_count: int = 0):
        """Send logs to RabbitMQ with retry logic."""
        current_retry = 0

        while current_retry <= self._max_retries:
            try:
                await self._ensure_channel()

                payload = {
                    "Type": "logs",
                    "ServiceName": self._service_name,
                    "Data": [asdict(log) for log in logs]
                }

                json_payload = json.dumps(payload, default=str)
                timestamp = datetime.now(timezone.utc)
                message_id = f"logs_{self._service_name}_{timestamp.strftime('%Y%m%d%H%M%S%f')[:-3]}_{uuid.uuid4().hex}"

                message = aio_pika.Message(
                    body=json_payload.encode("utf-8"),
                    content_type="application/json",
                    message_id=message_id,
                    correlation_id="blocks-lmt-service-logs",
                    type="logs",
                    headers={
                        "serviceName": self._service_name,
                        "timestamp": timestamp.isoformat(),
                        "source": "LogsSender",
                        "type": "logs",
                    },
                )

                async with self._publish_lock:
                    await self._exchange.publish(
                        message,
                        routing_key=self.LOGS_ROUTING_KEY,
                        mandatory=True,
                    )
                return

            except Exception as ex:
                print(f"Exception sending logs to RabbitMQ: {ex}, Retry: {current_retry}/{self._max_retries}")

            current_retry += 1

            if current_retry <= self._max_retries:
                delay = 2 ** (current_retry - 1)
                await asyncio.sleep(delay)

        # Queue for later retry if all retries failed
        if len(self._failed_log_batches) < self._max_failed_batches:
            failed_batch = FailedLogBatch(
                Logs=logs,
                RetryCount=retry_count + 1,
                NextRetryTime=datetime.now(timezone.utc) + timedelta(minutes=2 ** retry_count)
            )
            self._failed_log_batches.append(failed_batch)
            print(f"Queued log batch for later retry. Failed batches in queue: {len(self._failed_log_batches)}")
        else:
            print(f"Failed log batch queue is full ({self._max_failed_batches}). Dropping batch.")

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
        """Retry failed log batches that are ready for retry."""
        async with self._retry_lock:
            now = datetime.now(timezone.utc)
            batches_to_retry = []
            batches_to_requeue = []

            while self._failed_log_batches:
                failed_batch = self._failed_log_batches.popleft()
                if failed_batch.NextRetryTime <= now:
                    batches_to_retry.append(failed_batch)
                else:
                    batches_to_requeue.append(failed_batch)

            for batch in batches_to_requeue:
                self._failed_log_batches.append(batch)

            for failed_batch in batches_to_retry:
                if failed_batch.RetryCount >= self._max_retries:
                    print(f"Log batch exceeded max retries ({self._max_retries}). Dropping batch with {len(failed_batch.Logs)} logs.")
                    continue

                print(f"Retrying failed log batch (Attempt {failed_batch.RetryCount + 1}/{self._max_retries})")
                await self.send_logs_async(failed_batch.Logs, failed_batch.RetryCount)

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


class RabbitMqLogBatcher:
    """
    Batches logs and sends them to RabbitMQ.
    Mirrors the AzureServiceBusLogBatcher but uses RabbitMQ transport.
    """

    def __init__(
        self,
        x_blocks_key: str,
        service_name: str,
        connection_string: str,
        batch_size: int = 100,
        flush_interval_sec: float = 5.0,
        max_retries: int = 3,
        max_failed_batches: int = 100
    ):
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.service_name = service_name
        self.x_blocks_key = x_blocks_key

        self._sender = LmtRabbitMqSender(
            service_name=service_name,
            connection_string=connection_string,
            max_retries=max_retries,
            max_failed_batches=max_failed_batches
        )

        self.queue = Queue()
        self._stop_event = threading.Event()
        self._semaphore = threading.Semaphore(1)

        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()

    def enqueue(self, record: logging.LogRecord):
        """Add a log record to the queue."""
        log_data = LogData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            Level=record.levelname,
            Message=record.getMessage(),
            Exception=str(record.exc_info[1]) if record.exc_info else "",
            ServiceName=self.service_name,
            Properties={
                "LoggerName": record.name,
                "TraceId": getattr(record, 'TraceId', None) or Activity.get_trace_id(),
                "SpanId": getattr(record, 'SpanId', None) or Activity.get_span_id(),
            },
            TenantId=getattr(record, 'TenantId', self.x_blocks_key)
        )

        self.queue.put(log_data)

    def _background_worker(self):
        """Background worker that batches and flushes logs."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        batch = []

        while not self._stop_event.is_set():
            try:
                log_data = self.queue.get(timeout=self.flush_interval_sec)
                batch.append(log_data)

                while len(batch) < self.batch_size:
                    try:
                        log_data = self.queue.get_nowait()
                        batch.append(log_data)
                    except Empty:
                        break

            except Empty:
                pass

            if batch:
                with self._semaphore:
                    try:
                        loop.run_until_complete(self._sender.send_logs_async(batch))
                    except Exception as e:
                        print(f"[RabbitMqLogBatcher] Send error: {e}")
                    finally:
                        batch.clear()

        # Flush remaining logs on shutdown
        if batch:
            with self._semaphore:
                try:
                    loop.run_until_complete(self._sender.send_logs_async(batch))
                except Exception as e:
                    print(f"[RabbitMqLogBatcher] Send error on shutdown: {e}")

        loop.run_until_complete(self._sender.close())
        loop.close()

    def stop(self):
        """Stop the background worker and flush remaining logs."""
        self._stop_event.set()
        self.worker_thread.join(timeout=10)


class RabbitMqHandler(logging.Handler):
    """
    Logging handler that sends logs to RabbitMQ.
    Mirrors AzureServiceBusHandler but uses RabbitMQ transport.
    """

    _log_batcher: Optional[RabbitMqLogBatcher] = None

    def __init__(
        self,
        x_blocks_key: str,
        service_name: str,
        connection_string: str,
        batch_size: int = 100,
        flush_interval_sec: float = 5.0,
        max_retries: int = 3,
        max_failed_batches: int = 100
    ):
        super().__init__()

        if not RabbitMqHandler._log_batcher:
            RabbitMqHandler._log_batcher = RabbitMqLogBatcher(
                x_blocks_key=x_blocks_key,
                service_name=service_name,
                connection_string=connection_string,
                batch_size=batch_size,
                flush_interval_sec=flush_interval_sec,
                max_retries=max_retries,
                max_failed_batches=max_failed_batches
            )

        self.log_batcher = RabbitMqHandler._log_batcher

    def emit(self, record: logging.LogRecord):
        """Emit a log record to the batcher."""
        try:
            self.log_batcher.enqueue(record)
        except Exception:
            self.handleError(record)
