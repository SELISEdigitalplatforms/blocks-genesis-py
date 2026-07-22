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
