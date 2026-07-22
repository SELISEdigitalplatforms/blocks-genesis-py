import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from blocks_genesis._message.rabbit_mq.rabbit_message_worker import RabbitMessageWorker
from blocks_genesis._message.message_configuration import (
    MessageConfiguration,
    RabbitMqConfiguration,
    ConsumerSubscription,
)

RMW = 'blocks_genesis._message.rabbit_mq.rabbit_message_worker.'


def _worker():
    cfg = MessageConfiguration(connection='amqp://h', service_name='svc')
    w = RabbitMessageWorker.__new__(RabbitMessageWorker)
    w._message_config = cfg
    w._consumer = MagicMock()
    w._consumer.process_message = AsyncMock()
    w._rabbit_mq_service = None
    w._tracer = MagicMock()
    w._stop_event = asyncio.Event()
    return w


def _msg(headers, body):
    m = MagicMock()
    m.headers = headers
    m.body = body
    m.ack = AsyncMock()
    return m


# ---------------- initialize ----------------

def test_initialize_success():
    w = _worker()
    with patch(RMW + 'RabbitMqService'):
        w.initialize()
    assert w._rabbit_mq_service is not None


def test_initialize_no_connection():
    w = _worker()
    w._message_config.connection = None
    with pytest.raises(ValueError):
        w.initialize()


# ---------------- run ----------------

@pytest.mark.asyncio
async def test_run_not_initialized():
    w = _worker()
    with pytest.raises(RuntimeError):
        await w.run()


@pytest.mark.asyncio
async def test_run_no_subscriptions():
    w = _worker()
    svc = MagicMock()
    svc.create_connection_async = AsyncMock()
    svc.initialize_subscriptions_async = AsyncMock()
    svc.channel = MagicMock()
    w._rabbit_mq_service = svc
    w._message_config.rabbit_mq_configuration = None
    await w.run()
    svc.create_connection_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_with_subscriptions():
    w = _worker()
    svc = MagicMock()
    svc.create_connection_async = AsyncMock()
    svc.initialize_subscriptions_async = AsyncMock()
    svc.channel = MagicMock()
    w._rabbit_mq_service = svc
    sub = ConsumerSubscription.bind_to_queue('q1')
    w._message_config.rabbit_mq_configuration = RabbitMqConfiguration(consumer_subscriptions=[sub])
    w._start_consuming = AsyncMock()
    w._stop_event.set()
    await w.run()
    w._start_consuming.assert_awaited_once()


# ---------------- _start_consuming ----------------

@pytest.mark.asyncio
async def test_start_consuming():
    w = _worker()
    channel = MagicMock()
    queue = MagicMock()
    queue.declaration_result.message_count = 3
    queue.consume = AsyncMock(return_value='ctag')
    channel.declare_queue = AsyncMock(return_value=queue)
    channel.set_qos = AsyncMock()
    sub = ConsumerSubscription.bind_to_queue('q1', prefetch_count=5)
    await w._start_consuming(channel, [sub])
    channel.declare_queue.assert_awaited_once()
    channel.set_qos.assert_awaited_once_with(prefetch_count=5)
    queue.consume.assert_awaited_once()


# ---------------- _make_callback ----------------

@pytest.mark.asyncio
async def test_make_callback_sequential():
    w = _worker()
    w._process_message = AsyncMock()
    sub = ConsumerSubscription.bind_to_queue('q1')
    cb = w._make_callback(sub)
    msg = MagicMock()
    await cb(msg)
    w._process_message.assert_awaited_once_with(msg, sub)


@pytest.mark.asyncio
async def test_make_callback_parallel():
    w = _worker()
    w._process_message = AsyncMock()
    sub = ConsumerSubscription.bind_to_queue('q1')
    sub.parallel_processing = True
    cb = w._make_callback(sub)
    msg = MagicMock()
    await cb(msg)
    await asyncio.sleep(0)
    w._process_message.assert_awaited()


@pytest.mark.asyncio
async def test_make_callback_exception():
    w = _worker()
    w._process_message = AsyncMock(side_effect=Exception('boom'))
    sub = ConsumerSubscription.bind_to_queue('q1')
    cb = w._make_callback(sub)
    await cb(MagicMock())


# ---------------- _process_message ----------------

@pytest.mark.asyncio
async def test_process_message_happy():
    w = _worker()
    w._consumer.process_message = AsyncMock()
    headers = {
        "TraceId": b"0af7651916cd43dd8448eb211c80319c",
        "SpanId": b"b7ad6b7169203331",
        "TenantId": b"tenant1",
        "SecurityContext": b'{"tenant_id": "t"}',
        "Baggage": b'{"k": "v"}',
    }
    body = json.dumps({"body": "{}", "type": "EVT"}).encode()
    msg = _msg(headers, body)
    sub = ConsumerSubscription.bind_to_queue('q1')
    with patch(RMW + 'BlocksContextManager'), patch(RMW + 'TraceContextTextMapPropagator') as TP:
        TP.return_value.extract.return_value = 'ctx'
        await w._process_message(msg, sub)
    msg.ack.assert_awaited_once()
    w._consumer.process_message.assert_awaited_once_with("EVT", "{}")


@pytest.mark.asyncio
async def test_process_message_headers_none_body_str_error():
    w = _worker()
    msg = _msg(None, "not-json-body")
    sub = ConsumerSubscription.bind_to_queue('q1')
    with patch(RMW + 'BlocksContextManager'), patch(RMW + 'TraceContextTextMapPropagator'):
        await w._process_message(msg, sub)
    msg.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_decode_variants_and_bad_baggage():
    w = _worker()
    w._consumer.process_message = AsyncMock()
    headers = {
        "TraceId": b"aaa",
        "SpanId": None,
        "TenantId": 123,
        "SecurityContext": "",
        "Baggage": b"not-json",
    }
    body = json.dumps({"body": "{}", "type": "E"}).encode()
    msg = _msg(headers, body)
    sub = ConsumerSubscription.bind_to_queue('q1')
    with patch(RMW + 'BlocksContextManager'), patch(RMW + 'TraceContextTextMapPropagator'):
        await w._process_message(msg, sub)
    msg.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_bad_security_context():
    w = _worker()
    w._consumer.process_message = AsyncMock()
    headers = {
        "TraceId": b"",
        "SpanId": b"",
        "SecurityContext": b"{bad json",
        "Baggage": b"{}",
    }
    body = json.dumps({"body": "{}", "type": "E"}).encode()
    msg = _msg(headers, body)
    sub = ConsumerSubscription.bind_to_queue('q1')
    with patch(RMW + 'BlocksContextManager'), patch(RMW + 'TraceContextTextMapPropagator'):
        await w._process_message(msg, sub)
    msg.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_trace_extract_raises():
    w = _worker()
    w._consumer.process_message = AsyncMock()
    headers = {
        "TraceId": b"tid",
        "SpanId": b"sid",
        "SecurityContext": b"",
        "Baggage": b"{}",
    }
    body = json.dumps({"body": "{}", "type": "E"}).encode()
    msg = _msg(headers, body)
    sub = ConsumerSubscription.bind_to_queue('q1')
    with patch(RMW + 'BlocksContextManager'), patch(RMW + 'TraceContextTextMapPropagator') as TP:
        TP.return_value.extract.side_effect = Exception('bad trace')
        await w._process_message(msg, sub)
    msg.ack.assert_awaited_once()


# ---------------- stop ----------------

@pytest.mark.asyncio
async def test_stop_with_service():
    w = _worker()
    svc = MagicMock(); svc.close = AsyncMock()
    w._rabbit_mq_service = svc
    await w.stop()
    assert w._stop_event.is_set()
    svc.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_no_service():
    w = _worker()
    w._rabbit_mq_service = None
    await w.stop()
    assert w._stop_event.is_set()


def test_init_real_constructor():
    cfg = MessageConfiguration(connection='amqp://h', service_name='svc')
    w = RabbitMessageWorker(cfg)
    assert w._message_config is cfg
    assert w._rabbit_mq_service is None
    assert w._consumer is not None
