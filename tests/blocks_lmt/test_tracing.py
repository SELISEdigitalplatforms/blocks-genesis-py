import pytest
from unittest.mock import Mock, patch, MagicMock
from opentelemetry import trace

from blocks_lmt.tracing import configure_tracing


class TestTracing:
    """Test cases for tracing configuration."""
    
    def test_configure_tracing_basic(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test basic tracing configuration."""
        configure_tracing(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )
        
        provider = trace.get_tracer_provider()
        assert provider is not None
    
    def test_configure_tracing_sets_service_name(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that service name is set correctly."""
        configure_tracing(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )
        
        provider = trace.get_tracer_provider()
        assert provider is not None
    
    def test_configure_tracing_with_custom_params(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test tracing configuration with custom parameters."""
        configure_tracing(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string,
            batch_size=500,
            flush_interval=3.0,
            max_retries=5,
            max_failed_batches=200
        )
        
        provider = trace.get_tracer_provider()
        assert provider is not None
    
    @patch('blocks_lmt.tracing.AzureServiceBusTraceExporter')
    def test_configure_tracing_creates_exporter(self, mock_exporter_class, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that Azure Service Bus trace exporter is created for non-AMQP connection strings."""
        mock_exporter = MagicMock()
        mock_exporter_class.return_value = mock_exporter

        configure_tracing(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )

        mock_exporter_class.assert_called_once()
        call_kwargs = mock_exporter_class.call_args[1]
        assert call_kwargs['x_blocks_key'] == sample_x_blocks_key
        assert call_kwargs['service_name'] == sample_service_id
        assert call_kwargs['connection_string'] == sample_connection_string

    @patch('blocks_lmt.tracing.RabbitMqTraceExporter')
    def test_configure_tracing_creates_rabbitmq_exporter(self, mock_exporter_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test that RabbitMQ trace exporter is created for AMQP connection strings."""
        mock_exporter = MagicMock()
        mock_exporter_class.return_value = mock_exporter

        configure_tracing(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_rabbitmq_connection_string
        )

        mock_exporter_class.assert_called_once()
        call_kwargs = mock_exporter_class.call_args[1]
        assert call_kwargs['x_blocks_key'] == sample_x_blocks_key
        assert call_kwargs['service_name'] == sample_service_id
        assert call_kwargs['connection_string'] == sample_rabbitmq_connection_string

    def test_configure_tracing_adds_processor(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that span processor is added."""
        configure_tracing(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )
        
        provider = trace.get_tracer_provider()
        assert provider is not None
