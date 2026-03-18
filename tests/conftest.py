import sys
from pathlib import Path

# Add project root to Python path - must be done VERY early, before any imports
project_root = Path(__file__).parent.parent.resolve()
project_root_str = str(project_root)

# Insert at the beginning of sys.path
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

# Also ensure it's in PYTHONPATH for subprocesses
import os
current_pythonpath = os.environ.get('PYTHONPATH', '')
if project_root_str not in current_pythonpath:
    os.environ['PYTHONPATH'] = f"{project_root_str}{os.pathsep}{current_pythonpath}"

import pytest
import logging
from unittest.mock import Mock, patch, MagicMock
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration before each test."""
    # Clear all handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    yield
    # Cleanup after test
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)


@pytest.fixture
def mock_tracer_provider():
    """Create a mock tracer provider for testing."""
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    return provider


@pytest.fixture
def mock_azure_servicebus_client():
    """Mock Azure Service Bus client."""
    with patch('blocks_lmt.azure_servicebus_log_exporter.ServiceBusClient') as mock_client:
        mock_sender = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.get_topic_sender.return_value = mock_sender
        mock_client.from_connection_string.return_value = mock_client_instance
        yield {
            'client': mock_client,
            'client_instance': mock_client_instance,
            'sender': mock_sender
        }


@pytest.fixture
def sample_connection_string():
    """Sample Azure Service Bus connection string for testing."""
    return "Endpoint=sb://test.servicebus.windows.net/;SharedAccessKeyName=test;SharedAccessKey=testkey;EntityPath=test-topic"


@pytest.fixture
def sample_x_blocks_key():
    """Sample x-blocks-key for testing."""
    return "test-tenant-key"


@pytest.fixture
def sample_service_id():
    """Sample service ID for testing."""
    return "test-service"


@pytest.fixture
def sample_rabbitmq_connection_string():
    """Sample RabbitMQ connection string for testing."""
    return "amqp://guest:guest@localhost:5672/"
