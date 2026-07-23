import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from blocks_genesis._tenant import tenant_service as ts
from blocks_genesis._tenant.tenant_service import TenantService, TenantCacheUpdateMessage
from blocks_genesis._tenant.tenant import Tenant

TS = 'blocks_genesis._tenant.tenant_service.'


def _svc():
    s = TenantService.__new__(TenantService)
    s.cache = MagicMock()
    s.database = MagicMock()
    s._tenant_cache = {}
    s._tenant_load_in_progress = {}
    s._update_channel = 'tenant::updates'
    s._collection_name = 'Tenants'
    s._initialized = False
    s._is_subscribed = False
    s._disposed = False
    s._initialize_lock = asyncio.Lock()
    s._tenant_load_lock = asyncio.Lock()
    return s


def _t(tid='t1', disabled=False):
    return Tenant(_id=tid, TenantId=tid, IsDisabled=disabled, DBName='db', DbConnectionString='conn')


@pytest.mark.asyncio
@patch(TS + 'AsyncIOMotorClient')
@patch(TS + 'CacheProvider')
@patch(TS + 'get_blocks_secret')
async def test_init_no_cache_raises(mock_gs, mock_cp, mock_motor):
    mock_cp.get_client.return_value = None
    with pytest.raises(RuntimeError):
        TenantService()


@pytest.mark.asyncio
async def test_get_tenant_none_id():
    assert await _svc().get_tenant(None) is None


@pytest.mark.asyncio
async def test_get_tenant_cache_hit():
    s = _svc(); t = _t('t1'); s._tenant_cache['t1'] = t
    assert await s.get_tenant('t1') is t


@pytest.mark.asyncio
async def test_get_tenant_loads_and_caches():
    s = _svc(); t = _t('t1')
    s._load_tenant_from_db = AsyncMock(return_value=t)
    assert await s.get_tenant('t1') is t
    assert s._tenant_cache['t1'] is t


@pytest.mark.asyncio
async def test_get_tenant_load_returns_none():
    s = _svc(); s._load_tenant_from_db = AsyncMock(return_value=None)
    assert await s.get_tenant('t1') is None


@pytest.mark.asyncio
async def test_get_tenant_by_domain_empty():
    assert await _svc().get_tenant_by_domain('') is None
    assert await _svc().get_tenant_by_domain('   ') is None


@pytest.mark.asyncio
async def test_get_tenant_by_domain_cache_hit():
    s = _svc()
    app = MagicMock(); app.domain = 'http://x.com'
    t = MagicMock(); t.tenant_id = 't1'; t.applications = [app]
    s._tenant_cache['t1'] = t
    assert await s.get_tenant_by_domain('x.com') is t


@pytest.mark.asyncio
async def test_get_tenant_by_domain_db_hit():
    s = _svc()
    s.database.__getitem__.return_value.find_one = AsyncMock(return_value={'_id': 't1', 'TenantId': 't1'})
    result = await s.get_tenant_by_domain('x.com')
    assert result is not None


@pytest.mark.asyncio
async def test_get_tenant_by_domain_db_exception():
    s = _svc()
    s.database.__getitem__.return_value.find_one = AsyncMock(side_effect=Exception('db'))
    assert await s.get_tenant_by_domain('https://x.com') is None


@pytest.mark.asyncio
async def test_get_db_connection_found_and_none():
    s = _svc(); s.get_tenant = AsyncMock(return_value=_t('t1'))
    assert await s.get_db_connection('t1') == ('db', 'conn')
    s.get_tenant = AsyncMock(return_value=None)
    assert await s.get_db_connection('t1') == (None, None)


def test_get_tenant_database_connection_strings():
    s = _svc(); s._tenant_cache = {'t1': _t('t1')}
    assert s.get_tenant_database_connection_strings() == {'t1': ('db', 'conn')}


@pytest.mark.asyncio
async def test_load_tenant_from_db_hit_none_exception():
    s = _svc()
    s.database.__getitem__.return_value.find_one = AsyncMock(return_value={'_id': 't1', 'TenantId': 't1'})
    assert await s._load_tenant_from_db('t1') is not None
    s.database.__getitem__.return_value.find_one = AsyncMock(return_value=None)
    assert await s._load_tenant_from_db('t1') is None
    s.database.__getitem__.return_value.find_one = AsyncMock(side_effect=Exception('x'))
    assert await s._load_tenant_from_db('t1') is None


@pytest.mark.asyncio
async def test_update_tenant_version_none_raises():
    with pytest.raises(ValueError):
        await _svc().update_tenant_version_async(None)


@pytest.mark.asyncio
async def test_update_tenant_version_invalid_and_publish_and_exception():
    s = _svc()
    await s.update_tenant_version_async(TenantCacheUpdateMessage(action='bogus', tenant_id='t1'))
    s.cache.publish_async = AsyncMock()
    await s.update_tenant_version_async(TenantCacheUpdateMessage(action='remove', tenant_id='t1'))
    s.cache.publish_async.assert_awaited()
    s.cache.publish_async = AsyncMock(side_effect=Exception('pub'))
    await s.update_tenant_version_async(TenantCacheUpdateMessage(action='remove', tenant_id='t1'))


def test_parse_cache_update_valid_invalid():
    s = _svc()
    good = TenantCacheUpdateMessage(action='remove', tenant_id='t1').model_dump_json(by_alias=True)
    assert s._parse_tenant_cache_update(good) is not None
    assert s._parse_tenant_cache_update('not json') is None


def test_resolve_tenant_id():
    s = _svc()
    assert s._resolve_tenant_id(TenantCacheUpdateMessage(action='remove', tenant_id='t1')) == 't1'
    assert s._resolve_tenant_id(TenantCacheUpdateMessage(action='upsert', tenant=_t('t2'))) == 't2'
    assert s._resolve_tenant_id(TenantCacheUpdateMessage(action='upsert')) is None


def test_normalize_cache_update():
    s = _svc()
    assert s._normalize_cache_update(TenantCacheUpdateMessage(action='bad')) is None
    assert s._normalize_cache_update(TenantCacheUpdateMessage(action='remove')) is None
    r = s._normalize_cache_update(TenantCacheUpdateMessage(action='remove', tenant_id='t1'))
    assert r.action == 'remove' and r.tenant_id == 't1'
    assert s._normalize_cache_update(TenantCacheUpdateMessage(action='upsert')) is None
    u = s._normalize_cache_update(TenantCacheUpdateMessage(action='upsert', tenant=_t('t2')))
    assert u.action == 'upsert' and u.tenant_id == 't2'


@pytest.mark.asyncio
async def test_apply_update_message_all_paths():
    s = _svc()
    s._tenant_cache = {'t1': _t('t1')}
    await s._apply_update_message(TenantCacheUpdateMessage(action='remove', tenant_id='t1'))
    assert 't1' not in s._tenant_cache
    await s._apply_update_message(TenantCacheUpdateMessage(action='upsert'))
    s._tenant_cache = {'t2': _t('t2')}
    await s._apply_update_message(TenantCacheUpdateMessage(action='upsert', tenant=_t('t2', disabled=True)))
    assert 't2' not in s._tenant_cache
    await s._apply_update_message(TenantCacheUpdateMessage(action='upsert', tenant=_t('t3')))
    assert 't3' in s._tenant_cache


@pytest.mark.asyncio
async def test_subscribe_to_updates_paths():
    s = _svc()
    s._is_subscribed = True
    await s._subscribe_to_updates()
    s2 = _svc(); s2.cache.subscribe_async = AsyncMock()
    await s2._subscribe_to_updates()
    assert s2._is_subscribed is True
    s3 = _svc(); s3.cache.subscribe_async = AsyncMock(side_effect=Exception('sub'))
    await s3._subscribe_to_updates()


@pytest.mark.asyncio
async def test_handle_update_wrapper():
    s = _svc()
    s._handle_update_wrapper('chan', 'msg')
    await asyncio.sleep(0)
    with patch(TS + 'asyncio') as mock_asyncio:
        mock_asyncio.create_task.side_effect = Exception('task')
        s._handle_update_wrapper('chan', 'msg')


@pytest.mark.asyncio
async def test_handle_update_paths():
    s = _svc()
    s._apply_update_message = AsyncMock()
    await s._handle_update('not json')
    await s._handle_update(TenantCacheUpdateMessage(action='bad', tenant_id='t1').model_dump_json(by_alias=True))
    await s._handle_update(TenantCacheUpdateMessage(action='remove', tenant_id='t1').model_dump_json(by_alias=True))
    s._apply_update_message.assert_awaited()
    s._parse_tenant_cache_update = MagicMock(side_effect=Exception('boom'))
    await s._handle_update('x')


@pytest.mark.asyncio
async def test_get_tenant_service_not_initialized():
    with patch(TS + '_tenant_service', None):
        with pytest.raises(RuntimeError):
            ts.get_tenant_service()


@pytest.mark.asyncio
async def test_initialize_and_early_return():
    s = _svc()
    s._subscribe_to_updates = AsyncMock()
    await s.initialize()
    assert s._initialized is True
    await s.initialize()


@pytest.mark.asyncio
async def test_dispose_async_all_paths():
    s = _svc()
    await s.dispose_async()
    assert s._disposed is True
    await s.dispose_async()
    s2 = _svc(); s2._is_subscribed = True
    s2.cache.unsubscribe_async = AsyncMock(side_effect=Exception('u'))
    await s2.dispose_async()
    s3 = _svc(); s3._is_subscribed = True
    s3.cache.unsubscribe_async = AsyncMock()
    await s3.dispose_async()


@pytest.mark.asyncio
async def test_get_tenant_by_domain_cache_no_match_then_db():
    s = _svc()
    app = MagicMock(); app.domain = 'http://other.com'
    t = MagicMock(); t.tenant_id = 't1'; t.applications = [app]
    s._tenant_cache['t1'] = t
    s.database.__getitem__.return_value.find_one = AsyncMock(return_value=None)
    assert await s.get_tenant_by_domain('x.com') is None


@pytest.mark.asyncio
async def test_apply_update_remove_non_str_id():
    s = _svc()
    await s._apply_update_message(TenantCacheUpdateMessage(action='remove'))


@pytest.mark.asyncio
async def test_initialize_tenant_service_reuses_existing():
    existing = MagicMock()
    existing.initialize = AsyncMock()
    with patch(TS + '_tenant_service', existing):
        await ts.initialize_tenant_service()
        existing.initialize.assert_awaited()


def test_get_tenant_service_returns_existing():
    sentinel = MagicMock()
    with patch(TS + '_tenant_service', sentinel):
        assert ts.get_tenant_service() is sentinel
