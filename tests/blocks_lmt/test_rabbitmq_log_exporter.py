import pytest
import logging
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

from blocks_lmt.azure_servicebus_log_exporter import LogData, FailedLogBatch
from blocks_lmt.rabbitmq_log_exporter import (
    LmtRabbitMqSender,
    RabbitMqLogBatcher,
    RabbitMqHandler
)


class TestLmtRabbitMqSender:
    """Test cases for LmtRabbitMqSender."""

    def test_get_exchange_name(self):
        """Test exchange name generation."""
        exchange_name = LmtRabbitMqSender._get_exchange_name("test-service")
        assert exchange_name == "lmt-test-service"

    @pytest.mark.asyncio
    @patch('blocks_lmt.rabbitmq_log_exporter.aio_pika')
    async def test_send_logs_async_success(self, mock_aio_pika, sample_rabbitmq_connection_string):
        """Test successful log sending to RabbitMQ."""
        mock_exchange = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.is_closed = False
        mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)

        mock_connection = AsyncMock()
        mock_connection.is_closed = False
        mock_connection.channel = AsyncMock(return_value=mock_channel)

        mock_aio_pika.connect_robust = AsyncMock(return_value=mock_connection)
        mock_aio_pika.ExchangeType.DIRECT = "direct"
        mock_aio_pika.Message = MagicMock()

        sender = LmtRabbitMqSender(
            service_name="test-service",
            connection_string=sample_rabbitmq_connection_string,
            max_retries=3,
            max_failed_batches=100
        )

        log_data = LogData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            Level="INFO",
            Message="Test log",
            Exception="",
            ServiceName="test-service",
            Properties={},
            TenantId="test-tenant"
        )

        await sender.send_logs_async([log_data])

        # Verify connection was established
        mock_aio_pika.connect_robust.assert_called_once()

        # Verify exchange was declared
        mock_channel.declare_exchange.assert_called_once_with(
            "lmt-test-service",
            "direct",
            durable=True,
            auto_delete=False,
        )

        # Verify message was published
        mock_exchange.publish.assert_called_once()

        # Verify message properties
        msg_call = mock_aio_pika.Message.call_args
        assert msg_call.kwargs["content_type"] == "application/json"
        assert msg_call.kwargs["correlation_id"] == "blocks-lmt-service-logs"
        assert msg_call.kwargs["type"] == "logs"
        assert msg_call.kwargs["headers"]["source"] == "LogsSender"
        assert msg_call.kwargs["headers"]["type"] == "logs"

        # Verify routing key
        publish_call = mock_exchange.publish.call_args
        assert publish_call.kwargs["routing_key"] == "logs"
        assert publish_call.kwargs["mandatory"] is True

        # Cleanup
        sender._stop_event.set()
        if sender._retry_timer:
            sender._retry_timer.join(timeout=1)

    @pytest.mark.asyncio
    @patch('blocks_lmt.rabbitmq_log_exporter.aio_pika')
    async def test_send_logs_async_retries_on_failure(self, mock_aio_pika, sample_rabbitmq_connection_string):
        """Test that send_logs_async retries on failure."""
        mock_exchange = AsyncMock()
        mock_exchange.publish.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            None  # Success on third try
        ]

        mock_channel = AsyncMock()
        mock_channel.is_closed = False
        mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)

        mock_connection = AsyncMock()
        mock_connection.is_closed = False
        mock_connection.channel = AsyncMock(return_value=mock_channel)

        mock_aio_pika.connect_robust = AsyncMock(return_value=mock_connection)
        mock_aio_pika.ExchangeType.DIRECT = "direct"
        mock_aio_pika.Message = MagicMock()

        sender = LmtRabbitMqSender(
            service_name="test-service",
            connection_string=sample_rabbitmq_connection_string,
            max_retries=3,
            max_failed_batches=100
        )

        log_data = LogData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            Level="INFO",
            Message="Test log",
            Exception="",
            ServiceName="test-service",
            Properties={},
            TenantId="test-tenant"
        )

        await sender.send_logs_async([log_data])

        # Verify it retried (should be called 3 times)
        assert mock_exchange.publish.call_count == 3

        # Cleanup
        sender._stop_event.set()
        if sender._retry_timer:
            sender._retry_timer.join(timeout=1)

    @pytest.mark.asyncio
    @patch('blocks_lmt.rabbitmq_log_exporter.aio_pika')
    async def test_send_logs_async_queues_failed_batch(self, mock_aio_pika, sample_rabbitmq_connection_string):
        """Test that failed batches are queued for later retry."""
        mock_exchange = AsyncMock()
        mock_exchange.publish.side_effect = Exception("Permanent failure")

        mock_channel = AsyncMock()
        mock_channel.is_closed = False
        mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)

        mock_connection = AsyncMock()
        mock_connection.is_closed = False
        mock_connection.channel = AsyncMock(return_value=mock_channel)

        mock_aio_pika.connect_robust = AsyncMock(return_value=mock_connection)
        mock_aio_pika.ExchangeType.DIRECT = "direct"
        mock_aio_pika.Message = MagicMock()

        sender = LmtRabbitMqSender(
            service_name="test-service",
            connection_string=sample_rabbitmq_connection_string,
            max_retries=2,
            max_failed_batches=100
        )

        log_data = LogData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            Level="INFO",
            Message="Test log",
            Exception="",
            ServiceName="test-service",
            Properties={},
            TenantId="test-tenant"
        )

        await sender.send_logs_async([log_data], retry_count=0)

        # Verify failed batch was queued
        assert len(sender._failed_log_batches) == 1
        failed_batch = sender._failed_log_batches[0]
        assert failed_batch.RetryCount == 1
        assert len(failed_batch.Logs) == 1

        # Cleanup
        sender._stop_event.set()
        if sender._retry_timer:
            sender._retry_timer.join(timeout=1)


class TestRabbitMqHandler:
    """Test cases for RabbitMqHandler."""

    @patch('blocks_lmt.rabbitmq_log_exporter.LmtRabbitMqSender')
    def test_handler_creation(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test handler creation."""
        # Reset singleton
        RabbitMqHandler._log_batcher = None

        handler = RabbitMqHandler(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string,
            batch_size=10,
            flush_interval_sec=1.0,
            max_retries=1,
            max_failed_batches=10
        )

        assert handler is not None
        assert hasattr(handler, 'log_batcher')

        # Cleanup singleton
        RabbitMqHandler._log_batcher = None

    @patch('blocks_lmt.rabbitmq_log_exporter.LmtRabbitMqSender')
    def test_handler_emit(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test handler emit method."""
        # Reset singleton
        RabbitMqHandler._log_batcher = None

        handler = RabbitMqHandler(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string,
            batch_size=10,
            flush_interval_sec=1.0,
            max_retries=1,
            max_failed_batches=10
        )

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None
        )

        handler.emit(record)

        # Cleanup singleton
        RabbitMqHandler._log_batcher = None


class TestRabbitMqLogBatcher:
    """Test cases for RabbitMqLogBatcher."""

    @patch('blocks_lmt.rabbitmq_log_exporter.LmtRabbitMqSender')
    def test_enqueue_converts_log_record_to_log_data(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test that enqueue converts LogRecord to LogData and adds to queue."""
        batcher = RabbitMqLogBatcher(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string,
            batch_size=10,
            flush_interval_sec=1.0,
            max_retries=1,
            max_failed_batches=10
        )

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test log message",
            args=(),
            exc_info=None
        )
        record.TenantId = "test-tenant"
        record.TraceId = "abc123traceid"
        record.SpanId = "def456spanid"

        batcher.enqueue(record)

        assert batcher.queue.qsize() == 1

        log_data = batcher.queue.get()

        assert log_data.Level == "INFO"
        assert log_data.Message == "Test log message"
        assert log_data.ServiceName == sample_service_id
        assert log_data.TenantId == "test-tenant"
        assert log_data.Properties["LoggerName"] == "test_logger"
        assert log_data.Properties["TraceId"] == "abc123traceid"
        assert log_data.Properties["SpanId"] == "def456spanid"

        batcher.stop()

    @patch('blocks_lmt.rabbitmq_log_exporter.LmtRabbitMqSender')
    def test_enqueue_uses_default_tenant_id(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test that enqueue uses default tenant ID when not in record."""
        batcher = RabbitMqLogBatcher(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string,
            batch_size=10,
            flush_interval_sec=1.0,
            max_retries=1,
            max_failed_batches=10
        )

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None
        )

        batcher.enqueue(record)
        log_data = batcher.queue.get()

        assert log_data.TenantId == sample_x_blocks_key

        batcher.stop()
