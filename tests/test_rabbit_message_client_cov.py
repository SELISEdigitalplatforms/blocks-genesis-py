import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from blocks_genesis._message.rabbit_mq.rabbit_message_client import RabbitMessageClient
from blocks_genesis._message.message_client import MessageClient
from blocks_genesis._message.consumer_message import ConsumerMessage
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_configuration import (
    MessageConfiguration,
    RabbitMqConfiguration,
)

RMC = 'blocks_genesis._message.rabbit_mq.rabbit_message_client.'


@dataclass
class _DC:
    x: int


def _client(ttl=0):
    cfg = MessageConfiguration(connection='amqp://h', service_name='svc')
    cfg.rabbit_mq_configuration = RabbitMqConfiguration(message_ttl_seconds=ttl)
    c = RabbitMessageClient.__new__(RabbitMessageClient)
    c._message_config = cfg
    svc = MagicMock()
    svc.create_connection_async = AsyncMock()
    svc.initialize_subscriptions_async = AsyncMock()
    svc.close = AsyncMock()
    ch = MagicMock()
    svc.channel = ch
    c._rabbit_mq_service = svc
    c._initialized = False
    c._init_lock = asyncio.Lock()
    return c, svc, ch


def _send_patches(stack, security_context=None):
    MAct = stack.enter_context(patch(RMC + 'Activity'))
    act = MagicMock()
    act.get_all_root_attributes.return_value = {}
    MAct.return_value.__enter__.return_value = act
    MAct.get_trace_id.return_value = 'tr'
    MAct.get_span_id.return_value = 'sp'
    MBCM = stack.enter_context(patch(RMC + 'BlocksContextManager'))
    MBCM.get_context.return_value = security_context
    stack.enter_context(patch(RMC + 'aio_pika'))
    return act


# ---------------- singleton lifecycle ----------------

def test_initialize_and_get_instance():
    RabbitMessageClient._instance = None
    MessageClient._active_instance = None
    cfg = MessageConfiguration(connection='amqp://h', service_name='svc')
    RabbitMessageClient.initialize(cfg)
    assert RabbitMessageClient.get_instance() is RabbitMessageClient._instance
    assert MessageClient._active_instance is RabbitMessageClient._instance
    RabbitMessageClient._instance = None
    MessageClient._active_instance = None


def test_initialize_idempotent():
    RabbitMessageClient._instance = None
    cfg = MessageConfiguration(connection='amqp://h', service_name='svc')
    RabbitMessageClient.initialize(cfg)
    first = RabbitMessageClient._instance
    RabbitMessageClient.initialize(cfg)
    assert RabbitMessageClient._instance is first
    RabbitMessageClient._instance = None
    MessageClient._active_instance = None


def test_get_instance_not_initialized():
    RabbitMessageClient._instance = None
    with pytest.raises(RuntimeError):
        RabbitMessageClient.get_instance()


# ---------------- _ensure_initialized_async ----------------

@pytest.mark.asyncio
async def test_ensure_initialized_early_return():
    c, svc, ch = _client()
    c._initialized = True
    await c._ensure_initialized_async()
    svc.create_connection_async.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_initialized_full():
    c, svc, ch = _client()
    await c._ensure_initialized_async()
    svc.create_connection_async.assert_awaited_once()
    ch.return_callbacks.add.assert_called_once_with(c._on_message_returned)
    svc.initialize_subscriptions_async.assert_awaited_once()
    assert c._initialized is True


@pytest.mark.asyncio
async def test_ensure_initialized_double_check():
    c, svc, ch = _client()
    calls = []

    async def cca(*a, **k):
        calls.append(1)
        await asyncio.sleep(0)

    svc.create_connection_async = cca
    await asyncio.gather(c._ensure_initialized_async(), c._ensure_initialized_async())
    assert len(calls) == 1
    assert c._initialized is True


# ---------------- _on_message_returned ----------------

def test_on_message_returned_with_body():
    c, _, _ = _client()
    msg = MagicMock(); msg.exchange = 'ex'; msg.routing_key = 'rk'; msg.body = b'hello'
    c._on_message_returned('collection', msg)


def test_on_message_returned_no_body():
    c, _, _ = _client()
    msg = MagicMock(); msg.exchange = 'ex'; msg.routing_key = 'rk'; msg.body = None
    c._on_message_returned(msg)


# ---------------- _serialize_payload ----------------

def test_serialize_basemodel():
    c, _, _ = _client()
    out = c._serialize_payload(EventMessage(body='b', type='t'))
    assert out == {'body': 'b', 'type': 't'}


def test_serialize_dataclass():
    c, _, _ = _client()
    assert c._serialize_payload(_DC(x=5)) == {'x': 5}


def test_serialize_dict():
    c, _, _ = _client()
    assert c._serialize_payload({'a': 1}) == {'a': 1}


def test_serialize_str():
    c, _, _ = _client()
    assert c._serialize_payload('hi') == {'message': 'hi'}


def test_serialize_unsupported():
    c, _, _ = _client()
    with pytest.raises(TypeError):
        c._serialize_payload(123)


# ---------------- _send_message_async ----------------

@pytest.mark.asyncio
async def test_send_non_exchange_ttl_context_sc():
    c, svc, ch = _client(ttl=5)
    c._initialized = True
    ch.default_exchange.publish = AsyncMock()
    sc = MagicMock(); sc.tenant_id = 't'; sc.model_dump.return_value = {}
    cm = ConsumerMessage(consumer_name='q', payload={'a': 1}, payload_type='T', context='ctx')
    with ExitStack() as stack:
        _send_patches(stack, security_context=sc)
        assert await c._send_message_async(cm, is_exchange=False) is True
    ch.default_exchange.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_exchange_no_ttl_no_context_sc_present():
    c, svc, ch = _client(ttl=0)
    c._initialized = True
    ex = MagicMock(); ex.publish = AsyncMock()
    ch.get_exchange = AsyncMock(return_value=ex)
    sc = MagicMock(); sc.tenant_id = 't'; sc.model_dump.return_value = {}
    cm = ConsumerMessage(consumer_name='ex1', payload={}, payload_type='T', routing_key='rk')
    with ExitStack() as stack:
        _send_patches(stack, security_context=sc)
        assert await c._send_message_async(cm, is_exchange=True) is True
    ch.get_exchange.assert_awaited_once_with('ex1')
    ex.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_no_security_context():
    c, svc, ch = _client(ttl=0)
    c._initialized = True
    ch.default_exchange.publish = AsyncMock()
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, security_context=None)
        assert await c._send_message_async(cm, is_exchange=False) is True


@pytest.mark.asyncio
async def test_send_publish_raises():
    c, svc, ch = _client(ttl=0)
    c._initialized = True
    ch.default_exchange.publish = AsyncMock(side_effect=Exception('boom'))
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, security_context=None)
        with pytest.raises(Exception):
            await c._send_message_async(cm, is_exchange=False)


# ---------------- public wrappers + close ----------------

@pytest.mark.asyncio
async def test_send_to_consumer_and_mass():
    c, svc, ch = _client(ttl=0)
    c._initialized = True
    ch.default_exchange.publish = AsyncMock()
    ex = MagicMock(); ex.publish = AsyncMock()
    ch.get_exchange = AsyncMock(return_value=ex)
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, security_context=None)
        assert await c.send_to_consumer_async(cm) is True
        assert await c.send_to_mass_consumer_async(cm) is True


@pytest.mark.asyncio
async def test_close():
    c, svc, ch = _client()
    c._initialized = True
    await c.close()
    svc.close.assert_awaited_once()
    assert c._initialized is False
