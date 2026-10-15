import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantService

TS = 'blocks_genesis._tenant.tenant_service.'


def _service(find_one):
    with patch(TS + 'get_blocks_secret'), patch(TS + 'CacheProvider') as cp, patch(TS + 'AsyncIOMotorClient'):
        cp.get_client.return_value = MagicMock(subscribe_async=AsyncMock())
        s = TenantService()
    s.database = MagicMock()
    s.database.__getitem__.return_value.find_one = find_one
    return s


@pytest.mark.asyncio
async def test_initialize_loads_root_tenant_with_empty_cache():
    s = _service(AsyncMock(return_value={'_id': 'root', 'TenantId': 'root'}))
    await s.initialize()
    assert s._tenant_cache == {}
    assert s.get_root_tenant_id() == 'root'


@pytest.mark.asyncio
async def test_async_getter_loads_once_on_cold_service():
    find_one = AsyncMock(return_value={'_id': 'root', 'TenantId': 'root'})
    s = _service(find_one)
    assert await s.get_root_tenant_id_async() == 'root'
    assert await s.get_root_tenant_id_async() == 'root'
    find_one.assert_awaited_once()
    call = find_one.await_args
    assert call.args[0] == {'IsRootTenant': True}
    assert call.kwargs['sort'] == [('IsDisabled', 1)]


@pytest.mark.asyncio
async def test_pubsub_upsert_cannot_change_root_tenant():
    s = _service(AsyncMock(return_value={'_id': 'root', 'TenantId': 'root'}))
    await s.initialize()
    message = json.dumps({
        'Action': 'upsert',
        'Tenant': {'_id': 'other', 'TenantId': 'other', 'IsRootTenant': True},
    })
    await s._handle_update(message)
    assert 'other' in s._tenant_cache
    assert s.get_root_tenant_id() == 'root'
    assert await s.get_root_tenant_id_async() == 'root'


@pytest.mark.asyncio
async def test_initialize_db_failure_is_logged_and_sync_getter_falls_back(caplog):
    s = _service(AsyncMock(side_effect=Exception('db down')))
    with caplog.at_level(logging.ERROR):
        await s.initialize()
    assert s._initialized is True
    assert 'root tenant' in caplog.text
    assert s.get_root_tenant_id() is None
    s._tenant_cache['cached'] = Tenant(_id='cached', TenantId='cached', IsRootTenant=True)
    assert s.get_root_tenant_id() == 'cached'


@pytest.mark.asyncio
async def test_no_root_tenant_in_database_gives_none():
    s = _service(AsyncMock(return_value=None))
    await s.initialize()
    assert s.get_root_tenant_id() is None
    assert await s.get_root_tenant_id_async() is None
