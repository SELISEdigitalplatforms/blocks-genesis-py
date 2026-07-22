import asyncio
import json
import logging
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from blocks_genesis._message.azure.azure_message_worker import AzureMessageWorker
from blocks_genesis._message.message_configuration import (
    MessageConfiguration,
    AzureServiceBusConfiguration,
)

AMW = 'blocks_genesis._message.azure.azure_message_worker.'


def _worker():
    cfg = MessageConfiguration(connection='sb://h', service_name='svc')
    cfg.azure_service_bus_configuration = AzureServiceBusConfiguration(queues=[], topics=[])
    w = AzureMessageWorker.__new__(AzureMessageWorker)
    w._logger = logging.getLogger('test')
    w._message_config = cfg
    w._consumer = MagicMock()
    w._consumer.process_message = AsyncMock()
    w._service_bus_client = None
    w._receivers = []
    w._active_message_renewals = {}
    w._tracer = MagicMock()
    return w


class FakeReceiver:
    def __init__(self, messages):
        self._messages = list(messages)
        self.abandon_message = AsyncMock()
        self.complete_message = AsyncMock()
        self.renew_message_lock = AsyncMock()
        self.close = AsyncMock()
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m
        return gen()


# ---------------- __init__ ----------------

def test_init_real_constructor():
    cfg = MessageConfiguration(connection='sb://h', service_name='svc')
    w = AzureMessageWorker(cfg)
    assert w._message_config is cfg
    assert w._receivers == []
    assert w._service_bus_client is None


# ---------------- initialize ----------------

def test_initialize_with_connection():
    w = _worker()
    with patch(AMW + 'ServiceBusClient') as SB, patch(AMW + 'get_blocks_secret') as GS:
        SB.from_connection_string.return_value = MagicMock()
        w.initialize()
    assert w._service_bus_client is not None
    GS.assert_not_called()


def test_initialize_fallback_secret():
    w = _worker()
    w._message_config.connection = None
    with patch(AMW + 'ServiceBusClient') as SB, patch(AMW + 'get_blocks_secret') as GS:
        GS.return_value.MessageConnectionString = 'sb://fallback'
        SB.from_connection_string.return_value = MagicMock()
        w.initialize()
    SB.from_connection_string.assert_called_once_with('sb://fallback')


def test_initialize_missing_connection():
    w = _worker()
    w._message_config.connection = None
    with patch(AMW + 'get_blocks_secret') as GS:
        GS.return_value.MessageConnectionString = None
        with pytest.raises(ValueError):
            w.initialize()


# ---------------- stop ----------------

@pytest.mark.asyncio
async def test_stop_full():
    w = _worker()
    ev = MagicMock()
    w._active_message_renewals = {'m': ev}
    rec1 = MagicMock(); rec1.close = AsyncMock()
    rec2 = MagicMock(); rec2.close = AsyncMock(side_effect=Exception('close fail'))
    w._receivers = [rec1, rec2]
    client = MagicMock(); client.close = AsyncMock()
    w._service_bus_client = client
    await w.stop()
    ev.set.assert_called_once()
    rec1.close.assert_awaited_once()
    client.close.assert_awaited_once()
    assert w._receivers == []
    assert w._active_message_renewals == {}


@pytest.mark.asyncio
async def test_stop_no_client():
    w = _worker()
    w._service_bus_client = None
    await w.stop()
    assert w._receivers == []


# ---------------- run ----------------

@pytest.mark.asyncio
async def test_run_not_initialized():
    w = _worker()
    w._service_bus_client = None
    with pytest.raises(ValueError):
        await w.run()


@pytest.mark.asyncio
async def test_run_queues_only():
    w = _worker()
    client = MagicMock()
    client.get_queue_receiver.return_value = MagicMock()
    w._service_bus_client = client
    w._message_config.azure_service_bus_configuration.queues = ['q1']
    w._message_config.azure_service_bus_configuration.topics = []
    w.safe_receiver_wrapper = AsyncMock()
    await w.run()
    client.get_queue_receiver.assert_called_once()
    assert len(w._receivers) == 1


@pytest.mark.asyncio
async def test_run_topics_raises_attribute_error():
    # Characterization of a real bug: run() reads
    # self._message_config.subscription_name (a dict) but MessageConfiguration
    # only has a get_subscription_name() method -> AttributeError on any topic.
    w = _worker()
    client = MagicMock()
    client.get_subscription_receiver.return_value = MagicMock()
    w._service_bus_client = client
    w._message_config.azure_service_bus_configuration.queues = []
    w._message_config.azure_service_bus_configuration.topics = ['t1']
    w.safe_receiver_wrapper = AsyncMock()
    with pytest.raises(AttributeError):
        await w.run()


# ---------------- safe_receiver_wrapper ----------------

@pytest.mark.asyncio
async def test_safe_receiver_wrapper_success():
    w = _worker()
    w.process_receiver = AsyncMock()
    rec = MagicMock()
    await w.safe_receiver_wrapper(rec, 'q')
    w.process_receiver.assert_awaited_once_with(rec)


@pytest.mark.asyncio
async def test_safe_receiver_wrapper_error():
    w = _worker()
    w.process_receiver = AsyncMock(side_effect=Exception('crash'))
    await w.safe_receiver_wrapper(MagicMock(), 'q')


# ---------------- process_receiver ----------------

@pytest.mark.asyncio
async def test_process_receiver_success():
    w = _worker()
    w.message_handler = AsyncMock()
    msg = MagicMock()
    rec = FakeReceiver([msg])
    await w.process_receiver(rec)
    w.message_handler.assert_awaited_once_with(rec, msg)


@pytest.mark.asyncio
async def test_process_receiver_handler_error_abandon():
    w = _worker()
    w.message_handler = AsyncMock(side_effect=Exception('x'))
    msg = MagicMock()
    rec = FakeReceiver([msg])
    await w.process_receiver(rec)
    rec.abandon_message.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_process_receiver_abandon_error():
    w = _worker()
    w.message_handler = AsyncMock(side_effect=Exception('x'))
    msg = MagicMock()
    rec = FakeReceiver([msg])
    rec.abandon_message = AsyncMock(side_effect=Exception('abandon fail'))
    await w.process_receiver(rec)


# ---------------- decode_app_properties ----------------

def test_decode_app_properties_none():
    assert _worker().decode_app_properties(None) == {}


def test_decode_app_properties_mixed():
    out = _worker().decode_app_properties({b'k1': b'v1', 'k2': 'v2', b'k3': 123})
    assert out == {'k1': 'v1', 'k2': 'v2', 'k3': 123}


# ---------------- message_handler ----------------

@pytest.mark.asyncio
async def test_message_handler_success():
    w = _worker()
    w._consumer.process_message = AsyncMock()
    w.start_auto_renewal_task = AsyncMock()

    def bodygen():
        yield b'{"body": "{}", "type": "EVT"}'

    message = MagicMock()
    message.message_id = 'm1'
    message.application_properties = {
        b"TraceId": b"0af7651916cd43dd8448eb211c80319c",
        b"SpanId": b"b7ad6b7169203331",
        b"TenantId": b"t1",
        b"SecurityContext": b'{"tenant_id":"t"}',
        b"Baggage": b'{"k":"v"}',
    }
    message.body = bodygen()
    receiver = MagicMock()
    receiver.complete_message = AsyncMock()
    receiver.abandon_message = AsyncMock()
    with patch(AMW + 'BlocksContextManager'), patch(AMW + 'TraceContextTextMapPropagator') as TP:
        TP.return_value.extract.return_value = 'ctx'
        await w.message_handler(receiver, message)
    receiver.complete_message.assert_awaited_once_with(message)
    w._consumer.process_message.assert_awaited_once_with("EVT", "{}")
    assert 'm1' not in w._active_message_renewals


@pytest.mark.asyncio
async def test_message_handler_body_none_error_abandon():
    w = _worker()
    w.start_auto_renewal_task = AsyncMock()
    message = MagicMock(); message.message_id = 'm2'
    message.application_properties = {}
    message.body = None
    receiver = MagicMock()
    receiver.abandon_message = AsyncMock()
    receiver.complete_message = AsyncMock()
    with patch(AMW + 'BlocksContextManager'), patch(AMW + 'TraceContextTextMapPropagator'):
        with pytest.raises(Exception):
            await w.message_handler(receiver, message)
    receiver.abandon_message.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_message_handler_int_body_bad_sc_bad_baggage():
    w = _worker()
    w.start_auto_renewal_task = AsyncMock()
    message = MagicMock(); message.message_id = 'm3'
    message.application_properties = {
        "SecurityContext": "{bad",
        "Baggage": "not-json",
    }
    message.body = 12345
    receiver = MagicMock()
    receiver.abandon_message = AsyncMock()
    receiver.complete_message = AsyncMock()
    with patch(AMW + 'BlocksContextManager'), patch(AMW + 'TraceContextTextMapPropagator'):
        with pytest.raises(Exception):
            await w.message_handler(receiver, message)


@pytest.mark.asyncio
async def test_message_handler_abandon_fails():
    w = _worker()
    w.start_auto_renewal_task = AsyncMock()
    message = MagicMock(); message.message_id = 'm4'
    message.application_properties = {}
    message.body = None
    receiver = MagicMock()
    receiver.abandon_message = AsyncMock(side_effect=Exception('abandon fail'))
    receiver.complete_message = AsyncMock()
    with patch(AMW + 'BlocksContextManager'), patch(AMW + 'TraceContextTextMapPropagator'):
        with pytest.raises(Exception):
            await w.message_handler(receiver, message)


# ---------------- start_auto_renewal_task ----------------

@pytest.mark.asyncio
async def test_renewal_already_cancelled():
    w = _worker()
    ev = asyncio.Event(); ev.set()
    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()
    await w.start_auto_renewal_task(msg, rec, ev)


@pytest.mark.asyncio
async def test_renewal_success_then_stop():
    w = _worker()
    cfg = w._message_config.azure_service_bus_configuration
    cfg.message_lock_renewal_interval_seconds = 0.01
    cfg.max_message_processing_time_in_minutes = 100
    ev = asyncio.Event()
    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()

    async def renew(m):
        ev.set()

    rec.renew_message_lock = AsyncMock(side_effect=renew)
    await w.start_auto_renewal_task(msg, rec, ev)
    rec.renew_message_lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_renewal_renew_fails():
    w = _worker()
    cfg = w._message_config.azure_service_bus_configuration
    cfg.message_lock_renewal_interval_seconds = 0.01
    cfg.max_message_processing_time_in_minutes = 100
    ev = asyncio.Event()
    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()
    rec.renew_message_lock = AsyncMock(side_effect=Exception('renew fail'))
    await w.start_auto_renewal_task(msg, rec, ev)
    assert ev.is_set()


@pytest.mark.asyncio
async def test_renewal_max_time_exceeded():
    w = _worker()
    cfg = w._message_config.azure_service_bus_configuration
    cfg.message_lock_renewal_interval_seconds = 0.01
    cfg.max_message_processing_time_in_minutes = 0
    ev = asyncio.Event()
    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()
    rec.renew_message_lock = AsyncMock()
    await w.start_auto_renewal_task(msg, rec, ev)
    rec.renew_message_lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_renewal_event_set_during_wait():
    w = _worker()
    cfg = w._message_config.azure_service_bus_configuration
    cfg.message_lock_renewal_interval_seconds = 5
    cfg.max_message_processing_time_in_minutes = 100
    ev = asyncio.Event()
    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()

    async def setter():
        await asyncio.sleep(0.01)
        ev.set()

    await asyncio.gather(w.start_auto_renewal_task(msg, rec, ev), setter())


@pytest.mark.asyncio
async def test_renewal_cancelled():
    w = _worker()
    cfg = w._message_config.azure_service_bus_configuration
    cfg.message_lock_renewal_interval_seconds = 5
    cfg.max_message_processing_time_in_minutes = 100
    ev = asyncio.Event()
    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()
    task = asyncio.ensure_future(w.start_auto_renewal_task(msg, rec, ev))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_renewal_generic_error():
    w = _worker()
    cfg = w._message_config.azure_service_bus_configuration
    cfg.message_lock_renewal_interval_seconds = 5
    cfg.max_message_processing_time_in_minutes = 100

    class BoomEvent:
        def is_set(self):
            return False
        async def wait(self):
            raise ValueError('boom')

    msg = MagicMock(); msg.message_id = 'm'
    rec = MagicMock()
    await w.start_auto_renewal_task(msg, rec, BoomEvent())
