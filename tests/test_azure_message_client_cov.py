import json
from asyncio import Lock
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from blocks_genesis._message.azure.azure_message_client import (
    AzureMessageClient,
    DateTimeEncoder,
)
from blocks_genesis._message.message_client import MessageClient
from blocks_genesis._message.consumer_message import ConsumerMessage
from blocks_genesis._message.event_message import EventMessage
from blocks_genesis._message.message_configuration import (
    MessageConfiguration,
    AzureServiceBusConfiguration,
)

AMC = 'blocks_genesis._message.azure.azure_message_client.'


@dataclass
class _DC:
    x: int


class _Ctx:
    def __init__(self, tenant_id='t'):
        self.tenant_id = tenant_id


class _FalsyCtx:
    def __init__(self):
        self.tenant_id = 't'
    def __bool__(self):
        return False


def _client():
    cfg = MessageConfiguration(connection='sb://h', service_name='svc')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration(queues=[], topics=[])
    c = AzureMessageClient.__new__(AzureMessageClient)
    c._message_config = cfg
    c._client = MagicMock()
    c._senders = {}
    c._sender_locks = defaultdict(Lock)
    return c


def _send_patches(stack, ctx):
    MAct = stack.enter_context(patch(AMC + 'Activity'))
    act = MagicMock()
    act.get_all_root_attributes.return_value = {}
    act.get_trace_id.return_value = 'tr'
    act.get_span_id.return_value = 'sp'
    MAct.return_value.__enter__.return_value = act
    MBCM = stack.enter_context(patch(AMC + 'BlocksContextManager'))
    MBCM.get_context.return_value = ctx
    # These tests cover send mechanics. The payload's shape is covered by
    # tests/test_context_transport.py, against the real builder.
    MBCM.create_sanitized_for_transport.return_value = {'TenantId': 't'}
    stack.enter_context(patch(AMC + 'ServiceBusMessage'))
    return act


# ---------------- DateTimeEncoder ----------------

def test_datetime_encoder_datetime():
    out = json.dumps({'d': datetime(2020, 1, 1)}, cls=DateTimeEncoder)
    assert '2020-01-01' in out


def test_datetime_encoder_unsupported():
    with pytest.raises(TypeError):
        json.dumps({'s': {1, 2}}, cls=DateTimeEncoder)


# ---------------- __init__ / _initialize_senders ----------------

def test_init_uses_connection_and_creates_senders():
    cfg = MessageConfiguration(connection='sb://conn', service_name='svc')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration(queues=['q1'], topics=['t1'])
    with patch(AMC + 'ServiceBusClient') as SB, patch(AMC + 'get_blocks_secret') as GS:
        sbc = MagicMock()
        SB.from_connection_string.return_value = sbc
        c = AzureMessageClient(cfg)
    assert 'q1' in c._senders and 't1' in c._senders
    sbc.get_queue_sender.assert_called_once_with(queue_name='q1')
    sbc.get_topic_sender.assert_called_once_with(topic_name='t1')
    GS.assert_not_called()


def test_init_connection_fallback_to_secret():
    cfg = MessageConfiguration(service_name='svc')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration()
    with patch(AMC + 'ServiceBusClient') as SB, patch(AMC + 'get_blocks_secret') as GS:
        GS.return_value.MessageConnectionString = 'sb://fallback'
        SB.from_connection_string.return_value = MagicMock()
        c = AzureMessageClient(cfg)
    assert c._message_config.connection == 'sb://fallback'
    SB.from_connection_string.assert_called_once_with('sb://fallback')


# ---------------- singleton ----------------

def test_initialize_and_get_instance():
    AzureMessageClient._instance = None
    MessageClient._active_instance = None
    cfg = MessageConfiguration(connection='sb://c', service_name='svc')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration(queues=[], topics=[])
    with patch(AMC + 'ServiceBusClient') as SB, patch(AMC + 'get_blocks_secret'):
        SB.from_connection_string.return_value = MagicMock()
        AzureMessageClient.initialize(cfg)
    assert AzureMessageClient.get_instance() is AzureMessageClient._instance
    assert MessageClient._active_instance is AzureMessageClient._instance
    AzureMessageClient._instance = None
    MessageClient._active_instance = None


def test_initialize_idempotent():
    AzureMessageClient._instance = None
    cfg = MessageConfiguration(connection='sb://c', service_name='svc')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration(queues=[], topics=[])
    with patch(AMC + 'ServiceBusClient') as SB, patch(AMC + 'get_blocks_secret'):
        SB.from_connection_string.return_value = MagicMock()
        AzureMessageClient.initialize(cfg)
        first = AzureMessageClient._instance
        AzureMessageClient.initialize(cfg)
    assert AzureMessageClient._instance is first
    AzureMessageClient._instance = None
    MessageClient._active_instance = None


def test_get_instance_not_initialized():
    AzureMessageClient._instance = None
    with pytest.raises(Exception):
        AzureMessageClient.get_instance()


# ---------------- _get_sender ----------------

@pytest.mark.asyncio
async def test_get_sender_cached():
    c = _client()
    s = MagicMock()
    c._senders['x'] = s
    assert await c._get_sender('x') is s


@pytest.mark.asyncio
async def test_get_sender_creates():
    c = _client()
    c._client.get_topic_sender.return_value = 'newsender'
    out = await c._get_sender('y')
    assert out == 'newsender'
    c._client.get_topic_sender.assert_called_once_with(topic_name='y')


@pytest.mark.asyncio
async def test_get_sender_creates_a_queue_sender_for_a_configured_queue():
    c = _client()
    c._message_config.azure_service_bus_configuration.queues = ['q']
    c._client.get_queue_sender.return_value = 'queuesender'
    assert await c._get_sender('q') == 'queuesender'
    c._client.get_queue_sender.assert_called_once_with(queue_name='q')
    c._client.get_topic_sender.assert_not_called()


@pytest.mark.asyncio
async def test_replace_sender_swaps_in_a_fresh_one_and_closes_the_old():
    """A failed send leaves the handler torn down, so the sender is rebuilt rather
    than handed to the next caller with a dead link."""
    c = _client()
    dead = MagicMock()
    dead.close = AsyncMock()
    c._senders['q'] = dead
    c._message_config.azure_service_bus_configuration.queues = ['q']
    c._client.get_queue_sender.return_value = 'fresh'

    assert await c._replace_sender('q') == 'fresh'
    assert c._senders['q'] == 'fresh'
    dead.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_sender_survives_a_close_that_throws():
    c = _client()
    dead = MagicMock()
    dead.close = AsyncMock(side_effect=RuntimeError('already gone'))
    c._senders['t'] = dead
    c._client.get_topic_sender.return_value = 'fresh'

    assert await c._replace_sender('t') == 'fresh'


# ---------------- _serialize_payload ----------------

def test_serialize_basemodel():
    assert _client()._serialize_payload(EventMessage(body='b', type='t')) == {'body': 'b', 'type': 't'}


def test_serialize_dataclass():
    assert _client()._serialize_payload(_DC(x=5)) == {'x': 5}


def test_serialize_dict():
    assert _client()._serialize_payload({'a': 1}) == {'a': 1}


def test_serialize_str():
    assert _client()._serialize_payload('hi') == {'message': 'hi'}


def test_serialize_unsupported():
    with pytest.raises(TypeError):
        _client()._serialize_payload(123)


# ---------------- _send_to_azure_bus_async ----------------

@pytest.mark.asyncio
async def test_concurrent_sends_never_overlap_inside_one_sender():
    """The sender is shared by every caller, and an async ServiceBusSender cannot be
    driven by two coroutines at once: its `_open` sets `_running` only after several
    awaits, so a second caller walks in and both rebuild the link, nulling `_handler`
    and `_session` under each other. That surfaces as a bare AttributeError from inside
    the SDK, so the sends have to be serialised per destination."""
    c = _client()
    state = {'inflight': 0, 'overlaps': 0, 'sends': 0}

    async def send_messages(_message):
        state['inflight'] += 1
        if state['inflight'] > 1:
            state['overlaps'] += 1
        await asyncio.sleep(0)  # the yield point where a real reconnect interleaves
        state['inflight'] -= 1
        state['sends'] += 1

    sender = MagicMock()
    sender.send_messages = send_messages
    c._senders['q'] = sender

    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        await asyncio.gather(*(
            c._send_to_azure_bus_async(
                ConsumerMessage(consumer_name='q', payload={}, payload_type='T'),
                is_topic=False,
            )
            for _ in range(12)
        ))

    assert state['sends'] == 12
    assert state['overlaps'] == 0, 'two coroutines were inside the same sender'


@pytest.mark.asyncio
async def test_sends_to_different_destinations_do_not_queue_behind_each_other():
    c = _client()
    assert c._sender_locks['q1'] is not c._sender_locks['q2']


@pytest.mark.asyncio
async def test_a_failed_send_rebuilds_the_sender_and_retries_once():
    """The SDK tears the handler down on failure, so the cached sender is left with a
    dead link. Retrying on the same object just fails again."""
    c = _client()
    c._message_config.azure_service_bus_configuration.queues = ['q']
    dead = MagicMock()
    dead.close = AsyncMock()
    dead.send_messages = AsyncMock(
        side_effect=AttributeError("'NoneType' object has no attribute 'create_sender_link'")
    )
    fresh = MagicMock()
    fresh.send_messages = AsyncMock()
    c._senders['q'] = dead
    c._client.get_queue_sender.return_value = fresh

    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        assert await c._send_to_azure_bus_async(cm, is_topic=False) is True

    dead.close.assert_awaited_once()
    fresh.send_messages.assert_awaited_once()
    assert c._senders['q'] is fresh


@pytest.mark.asyncio
async def test_a_send_that_fails_twice_still_raises():
    """One rebuild, not a loop -- the caller's own retry policy owns what happens next."""
    c = _client()
    sender = MagicMock()
    sender.close = AsyncMock()
    sender.send_messages = AsyncMock(side_effect=RuntimeError('still down'))
    c._senders['q'] = sender
    c._client.get_topic_sender.return_value = sender

    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        with pytest.raises(RuntimeError):
            await c._send_to_azure_bus_async(cm, is_topic=False)
    assert sender.send_messages.await_count == 2


@pytest.mark.asyncio
async def test_send_queue_context_string():
    c = _client()
    sender = MagicMock(); sender.send_messages = AsyncMock()
    c._senders['q'] = sender
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T', context='ctx')
    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        assert await c._send_to_azure_bus_async(cm, is_topic=False) is True
    sender.send_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_topic_context_none_ctx_truthy():
    c = _client()
    sender = MagicMock(); sender.send_messages = AsyncMock()
    c._senders['t'] = sender
    cm = ConsumerMessage(consumer_name='t', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        assert await c._send_to_azure_bus_async(cm, is_topic=True) is True


@pytest.mark.asyncio
async def test_send_falsy_context_else_branches():
    c = _client()
    sender = MagicMock(); sender.send_messages = AsyncMock()
    c._senders['q'] = sender
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T')
    with ExitStack() as stack:
        _send_patches(stack, _FalsyCtx())
        assert await c._send_to_azure_bus_async(cm, is_topic=False) is True


@pytest.mark.asyncio
async def test_send_raises():
    c = _client()
    sender = MagicMock(); sender.send_messages = AsyncMock(side_effect=Exception('x'))
    c._senders['q'] = sender
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T', context='ctx')
    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        with pytest.raises(Exception):
            await c._send_to_azure_bus_async(cm, is_topic=False)


# ---------------- wrappers + close ----------------

@pytest.mark.asyncio
async def test_wrappers():
    c = _client()
    sender = MagicMock(); sender.send_messages = AsyncMock()
    c._senders['q'] = sender
    cm = ConsumerMessage(consumer_name='q', payload={}, payload_type='T', context='ctx')
    with ExitStack() as stack:
        _send_patches(stack, _Ctx())
        assert await c.send_to_consumer_async(cm) is True
        assert await c.send_to_mass_consumer_async(cm) is True


@pytest.mark.asyncio
async def test_close():
    c = _client()
    c._client.close = AsyncMock()
    await c.close()
    c._client.close.assert_awaited_once()
