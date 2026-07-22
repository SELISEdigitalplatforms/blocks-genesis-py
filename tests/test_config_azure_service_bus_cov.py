import pytest
from unittest.mock import patch, MagicMock

from blocks_genesis._message.azure.config_azure_service_bus import ConfigAzureServiceBus
from blocks_genesis._message.message_configuration import (
    MessageConfiguration,
    AzureServiceBusConfiguration,
)

CAB = 'blocks_genesis._message.azure.config_azure_service_bus.'


def _cfg(queues=None, topics=None):
    c = MessageConfiguration(connection='sb://h', service_name='svc')
    c.azure_service_bus_configuration = AzureServiceBusConfiguration(
        queues=queues or [], topics=topics or []
    )
    return c


def test_configure_success_creates_all():
    admin = MagicMock()
    admin.get_queue.side_effect = Exception('missing')
    admin.get_topic.side_effect = Exception('missing')
    admin.get_subscription.side_effect = Exception('missing')
    with patch(CAB + 'ServiceBusAdministrationClient') as SB:
        SB.from_connection_string.return_value = admin
        ConfigAzureServiceBus.configure_queue_and_topic(_cfg(queues=['q1'], topics=['t1']))
    admin.create_queue.assert_called_once()
    admin.create_topic.assert_called_once()
    admin.create_subscription.assert_called_once()


def test_configure_exception_reraises():
    with patch(CAB + 'ServiceBusAdministrationClient') as SB:
        SB.from_connection_string.side_effect = Exception('boom')
        with pytest.raises(Exception):
            ConfigAzureServiceBus.configure_queue_and_topic(_cfg())


def test_create_queues_exists_skip():
    admin = MagicMock()
    admin.get_queue.return_value = object()
    ConfigAzureServiceBus._admin_client = admin
    ConfigAzureServiceBus._message_config = _cfg(queues=['q1'])
    ConfigAzureServiceBus._create_queues()
    admin.create_queue.assert_not_called()


def test_create_queues_empty():
    admin = MagicMock()
    ConfigAzureServiceBus._admin_client = admin
    ConfigAzureServiceBus._message_config = _cfg(queues=[])
    ConfigAzureServiceBus._create_queues()
    admin.create_queue.assert_not_called()


def test_create_topics_exists_skip_but_makes_subscription():
    admin = MagicMock()
    admin.get_topic.return_value = object()
    admin.get_subscription.side_effect = Exception('missing')
    ConfigAzureServiceBus._admin_client = admin
    ConfigAzureServiceBus._message_config = _cfg(topics=['t1'])
    ConfigAzureServiceBus._create_topics_and_subscriptions()
    admin.create_topic.assert_not_called()
    admin.create_subscription.assert_called_once()


def test_create_topics_empty():
    admin = MagicMock()
    ConfigAzureServiceBus._admin_client = admin
    ConfigAzureServiceBus._message_config = _cfg(topics=[])
    ConfigAzureServiceBus._create_topics_and_subscriptions()
    admin.create_topic.assert_not_called()


def test_create_subscription_exists_returns():
    admin = MagicMock()
    admin.get_subscription.return_value = object()
    ConfigAzureServiceBus._admin_client = admin
    ConfigAzureServiceBus._message_config = _cfg()
    ConfigAzureServiceBus._create_subscription('t1')
    admin.create_subscription.assert_not_called()
