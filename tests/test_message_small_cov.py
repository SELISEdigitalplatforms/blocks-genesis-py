import pytest
from unittest.mock import patch

from blocks_genesis._message.consumer import Consumer
from blocks_genesis._message.message_client import MessageClient
from blocks_genesis._message.consumer_message import ConsumerMessage
from blocks_genesis._message.event_registry import EventRegistry
from blocks_genesis._message.message_configuration import (
    AzureServiceBusConfiguration,
    ConsumerSubscription,
    RabbitMqConfiguration,
    MessageConfiguration,
    _get_provider,
)

RESOLVE = 'blocks_genesis._message.consumer.EventRegistry.resolve'


# ---------------- consumer.py ----------------

@pytest.mark.asyncio
async def test_consumer_callable_handler():
    seen = {}
    async def handler(data):
        seen['data'] = data
    with patch(RESOLVE, return_value=handler):
        await Consumer().process_message('t', '{"a": 1}')
    assert seen['data'] == {'a': 1}


@pytest.mark.asyncio
async def test_consumer_not_callable_no_handle():
    with patch(RESOLVE, return_value=object()):
        with pytest.raises(TypeError):
            await Consumer().process_message('t', '{"a": 1}')


@pytest.mark.asyncio
async def test_consumer_non_callable_with_handle_raises():
    # Characterization of current behavior: a non-callable object that has a
    # `handle` attribute enters the elif and then fails at `handler()` because
    # it is not callable. (Latent bug flagged for the user.)
    class WithHandle:
        async def handle(self, data):
            pass
    with patch(RESOLVE, return_value=WithHandle()):
        with pytest.raises(TypeError):
            await Consumer().process_message('t', '{"a": 1}')


# ---------------- message_client.py ----------------

class _Impl(MessageClient):
    async def send_to_consumer_async(self, m):
        return await super().send_to_consumer_async(m)
    async def send_to_mass_consumer_async(self, m):
        return await super().send_to_mass_consumer_async(m)


@pytest.mark.asyncio
async def test_message_client_abstract_bodies():
    impl = _Impl()
    msg = ConsumerMessage(consumer_name='c', payload={}, payload_type='t')
    assert await impl.send_to_consumer_async(msg) is None
    assert await impl.send_to_mass_consumer_async(msg) is None


def test_message_client_get_instance_none():
    MessageClient._active_instance = None
    with pytest.raises(RuntimeError):
        MessageClient.get_instance()


def test_message_client_set_and_get():
    impl = _Impl()
    MessageClient.set_active_instance(impl)
    assert MessageClient.get_instance() is impl
    MessageClient._active_instance = None


# ---------------- event_registry.py ----------------

def test_event_registry_invalid_type():
    with pytest.raises(ValueError):
        EventRegistry.register('')
    with pytest.raises(ValueError):
        EventRegistry.register(123)


def test_event_registry_register_and_resolve():
    EventRegistry._handlers.pop('evt_reg_x', None)
    @EventRegistry.register('evt_reg_x')
    def h(d):
        return d
    assert EventRegistry.resolve('evt_reg_x') is h
    EventRegistry._handlers.pop('evt_reg_x', None)


def test_event_registry_duplicate():
    EventRegistry._handlers.pop('evt_reg_y', None)
    @EventRegistry.register('evt_reg_y')
    def h(d):
        return d
    with pytest.raises(KeyError):
        @EventRegistry.register('evt_reg_y')
        def h2(d):
            return d
    EventRegistry._handlers.pop('evt_reg_y', None)


def test_event_registry_resolve_missing():
    with pytest.raises(ValueError):
        EventRegistry.resolve('evt_reg_missing_zzz')


# ---------------- message_configuration.py ----------------

def test_set_queues_filters_and_lowercases():
    cfg = AzureServiceBusConfiguration()
    cfg.set_queues(['A', ' ', '', 'B '])
    assert cfg.queues == ['a', 'b ']


def test_set_topics_filters_and_lowercases():
    cfg = AzureServiceBusConfiguration()
    cfg.set_topics(['T1', '  ', 'T2'])
    assert cfg.topics == ['t1', 't2']


def test_bind_to_queue():
    sub = ConsumerSubscription.bind_to_queue('myq', prefetch_count=7)
    assert sub.queue_name == 'myq'
    assert sub.exchange_name == ''
    assert sub.prefetch_count == 7


def test_bind_to_queue_via_exchange():
    sub = ConsumerSubscription.bind_to_queue_via_exchange('myq', 'myex', prefetch_count=3, parallel_processing=True)
    assert sub.queue_name == 'myq'
    assert sub.exchange_name == 'myex'
    assert sub.parallel_processing is True


def test_get_subscription_name():
    cfg = MessageConfiguration(service_name='svc')
    assert cfg.get_subscription_name('topicX') == 'topicX_sub_svc'


def test_resolve_provider_already_configured():
    cfg = MessageConfiguration(connection='amqp://h')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration()
    cfg.resolve_provider()
    assert cfg.rabbit_mq_configuration is None


def test_resolve_provider_no_connection():
    cfg = MessageConfiguration()
    cfg.resolve_provider()
    assert cfg.azure_service_bus_configuration is None
    assert cfg.rabbit_mq_configuration is None


def test_resolve_provider_rabbitmq():
    existing = ConsumerSubscription.bind_to_queue('pre')
    cfg = MessageConfiguration(connection='amqps://host', queues=['q1'], consumer_subscriptions=[existing])
    cfg.resolve_provider()
    assert cfg.rabbit_mq_configuration is not None
    names = [s.queue_name for s in cfg.rabbit_mq_configuration.consumer_subscriptions]
    assert 'q1' in names and 'pre' in names


def test_resolve_provider_azure():
    cfg = MessageConfiguration(connection='sb://host', queues=['q1'], topics=['t1'])
    cfg.resolve_provider()
    assert cfg.azure_service_bus_configuration is not None
    assert cfg.azure_service_bus_configuration.queues == ['q1']
    assert cfg.azure_service_bus_configuration.topics == ['t1']


def test_get_provider_variants():
    assert _get_provider('amqp://host') == 'rabbitmq'
    assert _get_provider('amqps://host') == 'rabbitmq'
    assert _get_provider('sb://host') == 'azure'


def test_get_provider_exception_defaults_azure():
    assert _get_provider(12345) == 'azure'
