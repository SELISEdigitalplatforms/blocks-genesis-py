import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from blocks_genesis._message.rabbit_mq.rabbit_mq_service import RabbitMqService
from blocks_genesis._message.message_configuration import (
    MessageConfiguration,
    RabbitMqConfiguration,
    ConsumerSubscription,
)

RMS = 'blocks_genesis._message.rabbit_mq.rabbit_mq_service.'


def _svc(connection='amqp://h'):
    return RabbitMqService(MessageConfiguration(connection=connection))


# ---- channel property ----

def test_channel_not_initialized():
    s = _svc()
    with pytest.raises(RuntimeError):
        _ = s.channel


def test_channel_returns():
    s = _svc()
    ch = MagicMock()
    s._channel = ch
    assert s.channel is ch


# ---- create_connection_async ----

@pytest.mark.asyncio
async def test_create_connection_success():
    s = _svc()
    conn = MagicMock(); conn.channel = AsyncMock(return_value='ch')
    with patch(RMS + 'aio_pika') as ap:
        ap.connect_robust = AsyncMock(return_value=conn)
        await s.create_connection_async()
    assert s._connection is conn
    assert s._channel == 'ch'


@pytest.mark.asyncio
async def test_create_connection_error():
    s = _svc()
    with patch(RMS + 'aio_pika') as ap:
        ap.connect_robust = AsyncMock(side_effect=Exception('boom'))
        with pytest.raises(Exception):
            await s.create_connection_async()


# ---- initialize_subscriptions_async ----

@pytest.mark.asyncio
async def test_init_subs_no_channel():
    s = _svc()
    with pytest.raises(RuntimeError):
        await s.initialize_subscriptions_async()


@pytest.mark.asyncio
async def test_init_subs_no_config():
    s = _svc()
    s._channel = MagicMock()
    s._config.rabbit_mq_configuration = None
    await s.initialize_subscriptions_async()


@pytest.mark.asyncio
async def test_init_subs_direct_queue():
    s = _svc()
    ch = MagicMock()
    ch.declare_queue = AsyncMock(return_value=MagicMock())
    ch.set_qos = AsyncMock()
    s._channel = ch
    sub = ConsumerSubscription.bind_to_queue('q1', prefetch_count=5)
    s._config.rabbit_mq_configuration = RabbitMqConfiguration(consumer_subscriptions=[sub])
    await s.initialize_subscriptions_async()
    ch.declare_queue.assert_awaited_once()
    ch.set_qos.assert_awaited_once_with(prefetch_count=5)


@pytest.mark.asyncio
async def test_init_subs_with_exchange():
    s = _svc()
    ch = MagicMock()
    queue = MagicMock(); queue.bind = AsyncMock()
    ch.declare_queue = AsyncMock(return_value=queue)
    ch.declare_exchange = AsyncMock(return_value='ex')
    ch.set_qos = AsyncMock()
    s._channel = ch
    sub = ConsumerSubscription.bind_to_queue_via_exchange('q1', 'ex1', prefetch_count=3)
    sub.routing_key = 'rk'
    sub.exchange_type = 'fanout'
    s._config.rabbit_mq_configuration = RabbitMqConfiguration(consumer_subscriptions=[sub])
    with patch(RMS + 'aio_pika') as ap:
        ap.ExchangeType = MagicMock(return_value='fanout-type')
        await s.initialize_subscriptions_async()
    ch.declare_exchange.assert_awaited_once()
    queue.bind.assert_awaited_once()
    ch.set_qos.assert_awaited_once_with(prefetch_count=3)


# ---- close ----

@pytest.mark.asyncio
async def test_close_all_open():
    s = _svc()
    ch = MagicMock(); ch.is_closed = False; ch.close = AsyncMock()
    conn = MagicMock(); conn.is_closed = False; conn.close = AsyncMock()
    s._channel = ch; s._connection = conn
    await s.close()
    ch.close.assert_awaited_once()
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_errors_warn():
    s = _svc()
    ch = MagicMock(); ch.is_closed = False; ch.close = AsyncMock(side_effect=Exception('x'))
    conn = MagicMock(); conn.is_closed = False; conn.close = AsyncMock(side_effect=Exception('y'))
    s._channel = ch; s._connection = conn
    await s.close()
    ch.close.assert_awaited_once()
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_none_or_closed():
    s = _svc()
    s._channel = None
    conn = MagicMock(); conn.is_closed = True; conn.close = AsyncMock()
    s._connection = conn
    await s.close()
    conn.close.assert_not_called()
