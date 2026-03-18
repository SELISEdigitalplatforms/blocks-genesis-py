import pytest
import logging
from unittest.mock import Mock, patch, MagicMock

from blocks_lmt.log_config import configure_logger


class TestLogConfig:
    """Test cases for log configuration."""
    
    def test_configure_logger_basic(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test basic logger configuration."""
        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string,
            batch_size=10,
            flush_interval_sec=1.0,
            max_retries=1,
            max_failed_batches=10
        )
        
        logger = logging.getLogger()
        assert len(logger.handlers) >= 1
    
    def test_configure_logger_sets_level(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that logger level is set correctly."""
        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )
        
        logger = logging.getLogger()
        assert logger.level <= logging.INFO
    
    def test_configure_logger_creates_handlers(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that handlers are created."""
        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )
        
        logger = logging.getLogger()
        assert len(logger.handlers) >= 1
    
    def test_configure_logger_clears_existing_handlers(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that existing handlers are cleared."""
        logger = logging.getLogger()
        dummy_handler = logging.StreamHandler()
        logger.addHandler(dummy_handler)
        
        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )
        
        assert dummy_handler not in logger.handlers
    
    def test_configure_logger_with_custom_params(self, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test logger configuration with custom parameters."""
        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string,
            batch_size=50,
            flush_interval_sec=2.5,
            max_retries=5,
            max_failed_batches=200
        )
        
        logger = logging.getLogger()
        assert logger is not None
    
    @patch('blocks_lmt.log_config.AzureServiceBusHandler')
    @patch('blocks_lmt.log_config.TraceContextFilter')
    def test_configure_logger_creates_servicebus_handler(self, mock_filter_class, mock_handler_class, sample_x_blocks_key, sample_service_id, sample_connection_string):
        """Test that Azure Service Bus handler is created for non-AMQP connection strings."""
        mock_handler = MagicMock()
        mock_handler.level = logging.NOTSET
        mock_handler_class.return_value = mock_handler
        mock_filter = MagicMock()
        mock_filter_class.return_value = mock_filter

        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_connection_string
        )

        mock_handler_class.assert_called_once()
        call_kwargs = mock_handler_class.call_args[1]
        assert call_kwargs['x_blocks_key'] == sample_x_blocks_key
        assert call_kwargs['service_name'] == sample_service_id
        assert call_kwargs['connection_string'] == sample_connection_string

    @patch('blocks_lmt.log_config.RabbitMqHandler')
    @patch('blocks_lmt.log_config.TraceContextFilter')
    def test_configure_logger_creates_rabbitmq_handler(self, mock_filter_class, mock_handler_class, sample_x_blocks_key, sample_service_id, sample_rabbitmq_connection_string):
        """Test that RabbitMQ handler is created for AMQP connection strings."""
        mock_handler = MagicMock()
        mock_handler.level = logging.NOTSET
        mock_handler_class.return_value = mock_handler
        mock_filter = MagicMock()
        mock_filter_class.return_value = mock_filter

        configure_logger(
            x_blocks_key=sample_x_blocks_key,
            blocks_service_id=sample_service_id,
            connection_string=sample_rabbitmq_connection_string
        )

        mock_handler_class.assert_called_once()
        call_kwargs = mock_handler_class.call_args[1]
        assert call_kwargs['x_blocks_key'] == sample_x_blocks_key
        assert call_kwargs['service_name'] == sample_service_id
        assert call_kwargs['connection_string'] == sample_rabbitmq_connection_string
