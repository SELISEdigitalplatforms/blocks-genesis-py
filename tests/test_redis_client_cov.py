import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from blocks_genesis._cache.redis_client import RedisClient

RC = 'blocks_genesis._cache.redis_client.'


def _client():
    c = RedisClient.__new__(RedisClient)
    c._sync_client = MagicMock()
    c._async_client = None
    c._redis_config = {}
    c._subscriptions = {}
    c._pubsub_tasks = {}
    c._disposed = False
    act = MagicMock()
    cm = MagicMock(); cm.__enter__ = MagicMock(return_value=act); cm.__exit__ = MagicMock(return_value=False)
    c._create_activity = MagicMock(return_value=cm)
    return c, act


@patch(RC + 'aioredis')
@patch(RC + 'redis')
@patch(RC + 'get_blocks_secret')
def test_init(mock_gs, mock_redis, mock_aio):
    mock_gs.return_value.CacheConnectionString = 'localhost:6379'
    mock_redis.connection.parse_url.return_value = {'host': 'localhost', 'port': 6379}
    c = RedisClient()
    assert c._sync_client is not None


@patch(RC + 'redis')
def test_parse_conn_user_and_timeouts(mock_redis):
    c = RedisClient.__new__(RedisClient)
    mock_redis.connection.parse_url.return_value = {'host': 'h', 'connectTimeout': '5000', 'syncTimeout': '3000'}
    cfg = c._parse_connection_string('h:6379,user=admin,connectTimeout=5000,syncTimeout=3000')
    assert cfg.get('username') == 'admin'
    assert cfg.get('socket_connect_timeout') == 5.0
    assert cfg.get('socket_timeout') == 3.0


@patch(RC + 'redis')
def test_parse_conn_username_present(mock_redis):
    c = RedisClient.__new__(RedisClient)
    mock_redis.connection.parse_url.return_value = {'host': 'h', 'username': 'existing'}
    cfg = c._parse_connection_string('h:6379')
    assert cfg.get('username') == 'existing'


@patch(RC + 'redis')
def test_parse_conn_no_user(mock_redis):
    c = RedisClient.__new__(RedisClient)
    mock_redis.connection.parse_url.return_value = {'host': 'h'}
    cfg = c._parse_connection_string('h:6379,password=p')
    assert 'username' not in cfg


@pytest.mark.asyncio
@patch(RC + 'aioredis')
async def test_get_async_client(mock_aio):
    c, _ = _client()
    client = await c._get_async_client()
    assert client is not None
    assert await c._get_async_client() is client


def test_cache_database():
    c, _ = _client()
    assert c.cache_database() is c._sync_client


@patch(RC + 'BlocksContextManager')
@patch(RC + 'Activity')
def test_create_activity_with_context(mock_act, mock_bcm):
    c = RedisClient.__new__(RedisClient)
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    a = MagicMock(); mock_act.start.return_value = a
    assert c._create_activity('k', 'op') is a


@patch(RC + 'BlocksContextManager')
@patch(RC + 'Activity')
def test_create_activity_no_context(mock_act, mock_bcm):
    c = RedisClient.__new__(RedisClient)
    mock_bcm.get_context.return_value = None
    mock_act.start.return_value = MagicMock()
    c._create_activity('k', 'op')


def test_coerce_bytes():
    assert RedisClient._coerce_bytes(None) is None
    assert RedisClient._coerce_bytes(b'x') == b'x'
    assert RedisClient._coerce_bytes('s') == b's'
    assert RedisClient._coerce_bytes(bytearray(b'a')) == b'a'


def test_key_exists():
    c, _ = _client(); c._sync_client.exists.return_value = 1
    assert c.key_exists('k') is True


def test_key_exists_error():
    c, _ = _client(); c._sync_client.exists.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.key_exists('k')


def test_add_string_ttl_and_no_ttl():
    c, _ = _client(); c._sync_client.setex.return_value = True; c._sync_client.set.return_value = True
    assert c.add_string_value('k', 'v', 10) is True
    assert c.add_string_value('k', 'v') is True


def test_add_string_error():
    c, _ = _client(); c._sync_client.set.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.add_string_value('k', 'v')


def test_get_string_found_and_none():
    c, _ = _client(); c._sync_client.get.return_value = 'val'
    assert c.get_string_value('k') == 'val'
    c._sync_client.get.return_value = None
    assert c.get_string_value('k') is None


def test_get_string_error():
    c, _ = _client(); c._sync_client.get.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.get_string_value('k')


def test_add_bytes_ttl_and_no_ttl():
    c, _ = _client(); c._sync_client.setex.return_value = True; c._sync_client.set.return_value = True
    assert c.add_bytes_value('k', b'v', 10) is True
    assert c.add_bytes_value('k', b'v') is True


def test_add_bytes_error():
    c, _ = _client(); c._sync_client.set.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.add_bytes_value('k', b'v')


def test_get_bytes_found_and_none():
    c, _ = _client(); c._sync_client.get.return_value = b'v'
    assert c.get_bytes_value('k') == b'v'
    c._sync_client.get.return_value = None
    assert c.get_bytes_value('k') is None


def test_get_bytes_error():
    c, _ = _client(); c._sync_client.get.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.get_bytes_value('k')


def test_remove_key():
    c, _ = _client(); c._sync_client.delete.return_value = 1
    assert c.remove_key('k') is True


def test_remove_key_error():
    c, _ = _client(); c._sync_client.delete.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.remove_key('k')


def test_add_hash_ttl_and_no_ttl():
    c, _ = _client(); c._sync_client.expire.return_value = True
    assert c.add_hash_value('k', {'a': 1}, 10) is True
    assert c.add_hash_value('k', {'a': 1}) is True


def test_add_hash_error():
    c, _ = _client(); c._sync_client.hset.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.add_hash_value('k', {'a': 1})


def test_get_hash_found_and_empty():
    c, _ = _client(); c._sync_client.hgetall.return_value = {'a': 1}
    assert c.get_hash_value('k') == {'a': 1}
    c._sync_client.hgetall.return_value = {}
    assert c.get_hash_value('k') == {}


def test_get_hash_error():
    c, _ = _client(); c._sync_client.hgetall.side_effect = Exception('x')
    with pytest.raises(Exception):
        c.get_hash_value('k')


def _aclient():
    c, act = _client()
    ac = MagicMock()
    c._get_async_client = AsyncMock(return_value=ac)
    return c, act, ac


@pytest.mark.asyncio
async def test_key_exists_async():
    c, _, ac = _aclient(); ac.exists = AsyncMock(return_value=1)
    assert await c.key_exists_async('k') is True


@pytest.mark.asyncio
async def test_key_exists_async_error():
    c, _, ac = _aclient(); ac.exists = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.key_exists_async('k')


@pytest.mark.asyncio
async def test_add_string_async():
    c, _, ac = _aclient(); ac.setex = AsyncMock(return_value=True); ac.set = AsyncMock(return_value=True)
    assert await c.add_string_value_async('k', 'v', 10) is True
    assert await c.add_string_value_async('k', 'v') is True


@pytest.mark.asyncio
async def test_add_string_async_error():
    c, _, ac = _aclient(); ac.set = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.add_string_value_async('k', 'v')


@pytest.mark.asyncio
async def test_get_string_async():
    c, _, ac = _aclient(); ac.get = AsyncMock(return_value='val')
    assert await c.get_string_value_async('k') == 'val'
    ac.get = AsyncMock(return_value=None)
    assert await c.get_string_value_async('k') is None


@pytest.mark.asyncio
async def test_get_string_async_error():
    c, _, ac = _aclient(); ac.get = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.get_string_value_async('k')


@pytest.mark.asyncio
async def test_add_bytes_async():
    c, _, ac = _aclient(); ac.setex = AsyncMock(return_value=True); ac.set = AsyncMock(return_value=True)
    assert await c.add_bytes_value_async('k', b'v', 10) is True
    assert await c.add_bytes_value_async('k', b'v') is True


@pytest.mark.asyncio
async def test_add_bytes_async_error():
    c, _, ac = _aclient(); ac.set = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.add_bytes_value_async('k', b'v')


@pytest.mark.asyncio
async def test_get_bytes_async():
    c, _, ac = _aclient(); ac.get = AsyncMock(return_value=b'v')
    assert await c.get_bytes_value_async('k') == b'v'
    ac.get = AsyncMock(return_value=None)
    assert await c.get_bytes_value_async('k') is None


@pytest.mark.asyncio
async def test_get_bytes_async_error():
    c, _, ac = _aclient(); ac.get = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.get_bytes_value_async('k')


@pytest.mark.asyncio
async def test_remove_key_async():
    c, _, ac = _aclient(); ac.delete = AsyncMock(return_value=1)
    assert await c.remove_key_async('k') is True


@pytest.mark.asyncio
async def test_remove_key_async_error():
    c, _, ac = _aclient(); ac.delete = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.remove_key_async('k')


@pytest.mark.asyncio
async def test_add_hash_async():
    c, _, ac = _aclient(); ac.hset = AsyncMock(); ac.expire = AsyncMock(return_value=True)
    assert await c.add_hash_value_async('k', {'a': 1}, 10) is True
    assert await c.add_hash_value_async('k', {'a': 1}) is True


@pytest.mark.asyncio
async def test_add_hash_async_error():
    c, _, ac = _aclient(); ac.hset = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.add_hash_value_async('k', {'a': 1})


@pytest.mark.asyncio
async def test_get_hash_async():
    c, _, ac = _aclient(); ac.hgetall = AsyncMock(return_value={'a': 1})
    assert await c.get_hash_value_async('k') == {'a': 1}
    ac.hgetall = AsyncMock(return_value={})
    assert await c.get_hash_value_async('k') == {}


@pytest.mark.asyncio
async def test_get_hash_async_error():
    c, _, ac = _aclient(); ac.hgetall = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.get_hash_value_async('k')


@pytest.mark.asyncio
async def test_publish_empty_channel():
    c, _, ac = _aclient()
    with pytest.raises(ValueError):
        await c.publish_async('', 'm')


@pytest.mark.asyncio
async def test_publish_success():
    c, _, ac = _aclient(); ac.publish = AsyncMock(return_value=3)
    assert await c.publish_async('ch', 'm') == 3


@pytest.mark.asyncio
async def test_publish_error():
    c, _, ac = _aclient(); ac.publish = AsyncMock(side_effect=Exception('x'))
    with pytest.raises(Exception):
        await c.publish_async('ch', 'm')


# ---------------- pub/sub: subscribe_async / unsubscribe_async ----------------

class FakeTask:
    def __init__(self, exc=None, cancel_exc=None):
        self._exc = exc
        self._cancel_exc = cancel_exc
        self.cancelled = False
    def cancel(self):
        self.cancelled = True
        if self._cancel_exc:
            raise self._cancel_exc
    def __await__(self):
        if self._exc:
            raise self._exc
        return iter([])


@pytest.mark.asyncio
async def test_subscribe_empty_channel():
    c, _, ac = _aclient()
    with pytest.raises(ValueError):
        await c.subscribe_async('', lambda a, b: None)


@pytest.mark.asyncio
async def test_subscribe_none_handler():
    c, _, ac = _aclient()
    with pytest.raises(ValueError):
        await c.subscribe_async('ch', None)


@pytest.mark.asyncio
async def test_subscribe_success():
    c, act, ac = _aclient()
    c._handle_subscription = MagicMock(return_value=None)
    pubsub = MagicMock(); pubsub.subscribe = AsyncMock()
    ac.pubsub = MagicMock(return_value=pubsub)
    with patch(RC + 'asyncio') as masync:
        masync.create_task.return_value = 'task-obj'
        h = lambda a, b: None
        await c.subscribe_async('ch', h)
    assert c._subscriptions['ch'] is h
    assert c._pubsub_tasks['ch'] == 'task-obj'
    act.set_property.assert_any_call('subscribed', True)


@pytest.mark.asyncio
async def test_subscribe_error_pops_subscription():
    c, act, ac = _aclient()
    ac.pubsub = MagicMock(side_effect=Exception('boom'))
    with pytest.raises(Exception):
        await c.subscribe_async('ch', lambda a, b: None)
    assert 'ch' not in c._subscriptions


@pytest.mark.asyncio
async def test_unsubscribe_empty_channel():
    c, _ = _client()
    with pytest.raises(ValueError):
        await c.unsubscribe_async('')


@pytest.mark.asyncio
async def test_unsubscribe_with_task():
    c, act = _client()
    t = FakeTask(exc=__import__('asyncio').CancelledError())
    c._pubsub_tasks = {'ch': t}
    c._subscriptions = {'ch': lambda a, b: None}
    await c.unsubscribe_async('ch')
    assert t.cancelled is True
    assert 'ch' not in c._pubsub_tasks
    assert 'ch' not in c._subscriptions
    act.set_property.assert_any_call('unsubscribed', True)


@pytest.mark.asyncio
async def test_unsubscribe_without_task():
    c, act = _client()
    c._pubsub_tasks = {}
    c._subscriptions = {'ch': lambda a, b: None}
    await c.unsubscribe_async('ch')
    assert 'ch' not in c._subscriptions


@pytest.mark.asyncio
async def test_unsubscribe_error():
    c, act = _client()
    c._pubsub_tasks = {'ch': FakeTask(cancel_exc=RuntimeError('x'))}
    with pytest.raises(RuntimeError):
        await c.unsubscribe_async('ch')


# ---------------- _handle_subscription ----------------

@pytest.mark.asyncio
async def test_handle_subscription_messages():
    c, _ = _client()
    handler = MagicMock()
    async def listen():
        yield {'type': 'subscribe', 'channel': b'ch', 'data': 1}
        yield {'type': 'message', 'channel': b'ch', 'data': b'hello'}
        yield {'type': 'message', 'channel': 'ch2', 'data': 'world'}
    pubsub = MagicMock()
    pubsub.listen = MagicMock(return_value=listen())
    pubsub.unsubscribe = AsyncMock(); pubsub.close = AsyncMock()
    await c._handle_subscription(pubsub, 'ch', handler)
    assert handler.call_count == 2
    handler.assert_any_call('ch', 'hello')
    handler.assert_any_call('ch2', 'world')
    pubsub.unsubscribe.assert_awaited_once_with('ch')
    pubsub.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_subscription_handler_error():
    c, _ = _client()
    c._logger = MagicMock()
    handler = MagicMock(side_effect=Exception('bad'))
    async def listen():
        yield {'type': 'message', 'channel': b'ch', 'data': b'x'}
    pubsub = MagicMock()
    pubsub.listen = MagicMock(return_value=listen())
    pubsub.unsubscribe = AsyncMock(); pubsub.close = AsyncMock()
    await c._handle_subscription(pubsub, 'ch', handler)
    assert c._logger.error.called
    pubsub.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_subscription_cancelled():
    c, _ = _client()
    c._logger = MagicMock()
    async def listen():
        raise __import__('asyncio').CancelledError()
        yield
    pubsub = MagicMock()
    pubsub.listen = MagicMock(return_value=listen())
    pubsub.unsubscribe = AsyncMock(); pubsub.close = AsyncMock()
    await c._handle_subscription(pubsub, 'ch', MagicMock())
    pubsub.unsubscribe.assert_awaited_once_with('ch')


@pytest.mark.asyncio
async def test_handle_subscription_outer_error():
    c, _ = _client()
    c._logger = MagicMock()
    async def listen():
        raise RuntimeError('boom')
        yield
    pubsub = MagicMock()
    pubsub.listen = MagicMock(return_value=listen())
    pubsub.unsubscribe = AsyncMock(); pubsub.close = AsyncMock()
    await c._handle_subscription(pubsub, 'ch', MagicMock())
    assert c._logger.error.called
    pubsub.close.assert_awaited_once()


# ---------------- dispose / dispose_async ----------------

def test_dispose_already_disposed():
    c, _ = _client()
    c._disposed = True
    sc = c._sync_client
    c.dispose()
    sc.close.assert_not_called()


def test_dispose_normal():
    c, _ = _client()
    c._subscriptions = {'a': 1, 'b': 2}
    c.dispose()
    assert c._disposed is True
    assert c._subscriptions == {}
    c._sync_client.close.assert_called_once()


def test_dispose_no_sync_client():
    c, _ = _client()
    c._sync_client = None
    c.dispose()
    assert c._disposed is True


@pytest.mark.asyncio
async def test_dispose_async_already_disposed():
    c, _ = _client()
    c._disposed = True
    await c.dispose_async()
    assert c._disposed is True


@pytest.mark.asyncio
async def test_dispose_async_full():
    c, _ = _client()
    t = FakeTask(exc=__import__('asyncio').CancelledError())
    c._pubsub_tasks = {'ch': t}
    c._subscriptions = {'ch': 1}
    ac = MagicMock(); ac.close = AsyncMock()
    c._async_client = ac
    await c.dispose_async()
    assert t.cancelled is True
    assert c._pubsub_tasks == {}
    assert c._subscriptions == {}
    ac.close.assert_awaited_once()
    c._sync_client.close.assert_called_once()
    assert c._disposed is True


@pytest.mark.asyncio
async def test_dispose_async_no_clients():
    c, _ = _client()
    c._async_client = None
    c._sync_client = None
    c._pubsub_tasks = {}
    await c.dispose_async()
    assert c._disposed is True


# ---------------- context managers ----------------

def test_context_manager_sync():
    c, _ = _client()
    c.dispose = MagicMock()
    with c as x:
        assert x is c
    c.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_context_manager_async():
    c, _ = _client()
    c.dispose_async = AsyncMock()
    async with c as x:
        assert x is c
    c.dispose_async.assert_awaited_once()
