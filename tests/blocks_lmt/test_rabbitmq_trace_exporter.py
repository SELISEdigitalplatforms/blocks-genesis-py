import pytest
import json
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult

from blocks_lmt.azure_servicebus_trace_exporter import TraceData, FailedTraceBatch
from blocks_lmt.rabbitmq_trace_exporter import (
    LmtRabbitMqTraceSender,
    RabbitMqTraceExporter
)


class TestLmtRabbitMqTraceSender:
    """Test cases for LmtRabbitMqTraceSender."""

    def test_get_exchange_name(self):
        """Test exchange name generation."""
        exchange_name = LmtRabbitMqTraceSender._get_exchange_name("test-service")
        assert exchange_name == "lmt-test-service"

    @pytest.mark.asyncio
    @patch('blocks_lmt.rabbitmq_trace_exporter.aio_pika')
    async def test_send_traces_async_success(self, mock_aio_pika, sample_rabbitmq_connection_string):
        """Test successful trace sending to RabbitMQ."""
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

        sender = LmtRabbitMqTraceSender(
            service_name="test-service",
            connection_string=sample_rabbitmq_connection_string,
            max_retries=3,
            max_failed_batches=100
        )

        trace_data = TraceData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            TraceId="test-trace-id",
            SpanId="test-span-id",
            ParentSpanId="",
            ParentId="",
            Kind="INTERNAL",
            ActivitySourceName="test-source",
            OperationName="test-operation",
            StartTime=datetime.now(timezone.utc).isoformat(),
            EndTime=datetime.now(timezone.utc).isoformat(),
            Duration=1.0,
            Attributes={},
            Status="OK",
            StatusDescription="",
            Baggage={},
            ServiceName="test-service",
            TenantId="test-tenant"
        )

        tenant_batches = {"test-tenant": [trace_data]}
        await sender.send_traces_async(tenant_batches)

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
        assert msg_call.kwargs["correlation_id"] == "blocks-lmt-service-traces"
        assert msg_call.kwargs["type"] == "traces"
        assert msg_call.kwargs["headers"]["source"] == "TracesSender"
        assert msg_call.kwargs["headers"]["type"] == "traces"

        # Verify routing key
        publish_call = mock_exchange.publish.call_args
        assert publish_call.kwargs["routing_key"] == "traces"
        assert publish_call.kwargs["mandatory"] is True

        # Cleanup
        sender._stop_event.set()
        if sender._retry_timer:
            sender._retry_timer.join(timeout=1)

    @pytest.mark.asyncio
    @patch('blocks_lmt.rabbitmq_trace_exporter.aio_pika')
    async def test_send_traces_async_retries_on_failure(self, mock_aio_pika, sample_rabbitmq_connection_string):
        """Test that send_traces_async retries on failure."""
        mock_exchange = AsyncMock()
        mock_exchange.publish.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            None
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

        sender = LmtRabbitMqTraceSender(
            service_name="test-service",
            connection_string=sample_rabbitmq_connection_string,
            max_retries=3,
            max_failed_batches=100
        )

        trace_data = TraceData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            TraceId="test-trace-id",
            SpanId="test-span-id",
            ParentSpanId="",
            ParentId="",
            Kind="INTERNAL",
            ActivitySourceName="test-source",
            OperationName="test-operation",
            StartTime=datetime.now(timezone.utc).isoformat(),
            EndTime=datetime.now(timezone.utc).isoformat(),
            Duration=1.0,
            Attributes={},
            Status="OK",
            StatusDescription="",
            Baggage={},
            ServiceName="test-service",
            TenantId="test-tenant"
        )

        tenant_batches = {"test-tenant": [trace_data]}
        await sender.send_traces_async(tenant_batches)

        assert mock_exchange.publish.call_count == 3

        # Cleanup
        sender._stop_event.set()
        if sender._retry_timer:
            sender._retry_timer.join(timeout=1)

    @pytest.mark.asyncio
    @patch('blocks_lmt.rabbitmq_trace_exporter.aio_pika')
    async def test_send_traces_async_queues_failed_batch(self, mock_aio_pika, sample_rabbitmq_connection_string):
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

        sender = LmtRabbitMqTraceSender(
            service_name="test-service",
            connection_string=sample_rabbitmq_connection_string,
            max_retries=2,
            max_failed_batches=100
        )

        trace_data = TraceData(
            Timestamp=datetime.now(timezone.utc).isoformat(),
            TraceId="test-trace-id",
            SpanId="test-span-id",
            ParentSpanId="",
            ParentId="",
            Kind="INTERNAL",
            ActivitySourceName="test-source",
            OperationName="test-operation",
            StartTime=datetime.now(timezone.utc).isoformat(),
            EndTime=datetime.now(timezone.utc).isoformat(),
            Duration=1.0,
            Attributes={},
            Status="OK",
            StatusDescription="",
            Baggage={},
            ServiceName="test-service",
            TenantId="test-tenant"
        )

        tenant_batches = {"test-tenant": [trace_data]}
        await sender.send_traces_async(tenant_batches, retry_count=0)

        assert len(sender._failed_trace_batches) == 1
        failed_batch = sender._failed_trace_batches[0]
        assert failed_batch.RetryCount == 1
        assert "test-tenant" in failed_batch.TenantBatches

        # Cleanup
        sender._stop_event.set()
        if sender._retry_timer:
            sender._retry_timer.join(timeout=1)


class TestRabbitMqTraceExporter:
    """Test cases for RabbitMqTraceExporter."""

    @patch('blocks_lmt.rabbitmq_trace_exporter.LmtRabbitMqTraceSender')
    def test_exporter_creation(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test exporter creation."""
        exporter = RabbitMqTraceExporter(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string,
            batch_size=100,
            flush_interval=5.0,
            max_retries=3,
            max_failed_batches=100
        )

        assert exporter is not None
        assert hasattr(exporter, '_sender')
        assert exporter._x_blocks_key == sample_x_blocks_key
        assert exporter._service_name == sample_service_id

    @patch('blocks_lmt.rabbitmq_trace_exporter.LmtRabbitMqTraceSender')
    def test_exporter_export_with_empty_spans(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test export with empty span list."""
        exporter = RabbitMqTraceExporter(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string
        )

        result = exporter.export([])
        assert result == SpanExportResult.SUCCESS

    @patch('blocks_lmt.rabbitmq_trace_exporter.LmtRabbitMqTraceSender')
    def test_exporter_handles_span_conversion_errors(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test that exporter handles errors during span conversion."""
        exporter = RabbitMqTraceExporter(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string
        )

        mock_span = Mock(spec=ReadableSpan)
        mock_span.get_span_context.side_effect = Exception("Conversion error")

        result = exporter.export([mock_span])
        assert result == SpanExportResult.FAILURE

    @patch('blocks_lmt.rabbitmq_trace_exporter.LmtRabbitMqTraceSender')
    def test_exporter_shutdown(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test exporter shutdown."""
        exporter = RabbitMqTraceExporter(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string
        )

        exporter.shutdown()

    @patch('blocks_lmt.rabbitmq_trace_exporter.LmtRabbitMqTraceSender')
    def test_exporter_force_flush(self, mock_sender_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test exporter force flush."""
        exporter = RabbitMqTraceExporter(
            x_blocks_key=sample_x_blocks_key,
            service_name=sample_service_id,
            connection_string=sample_rabbitmq_connection_string
        )

        result = exporter.force_flush()
        assert result is True
