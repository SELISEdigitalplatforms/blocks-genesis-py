import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._tenant import tenant_service

@pytest.mark.asyncio
@patch('blocks_genesis._tenant.tenant_service.get_blocks_secret')
@patch('blocks_genesis._tenant.tenant_service.CacheProvider')
@patch('blocks_genesis._tenant.tenant_service.AsyncIOMotorClient')
async def test_tenant_service_init(mock_motor, mock_cache_provider, mock_get_secret):
    mock_get_secret.return_value.DatabaseConnectionString = 'conn'
    mock_get_secret.return_value.RootDatabaseName = 'rootdb'
    mock_cache_provider.get_client.return_value = MagicMock()
    service = tenant_service.TenantService()
    assert service.database is not None

@pytest.mark.asyncio
@patch('blocks_genesis._tenant.tenant_service.TenantService._load_tenants', new_callable=AsyncMock)
@patch('blocks_genesis._tenant.tenant_service.CacheProvider')
@patch('blocks_genesis._tenant.tenant_service.get_blocks_secret')
@patch('blocks_genesis._tenant.tenant_service.AsyncIOMotorClient')
async def test_initialize(mock_motor, mock_get_secret, mock_cache_provider, mock_load_tenants):
    mock_get_secret.return_value.DatabaseConnectionString = 'conn'
    mock_get_secret.return_value.RootDatabaseName = 'rootdb'
    mock_cache_provider.get_client.return_value = MagicMock()
    service = tenant_service.TenantService()
    await service.initialize()
    assert service._initialized is True

@pytest.mark.asyncio
@patch('blocks_genesis._tenant.tenant_service.TenantService._load_tenant_from_db', new_callable=AsyncMock)
@patch('blocks_genesis._tenant.tenant_service.get_blocks_secret')
@patch('blocks_genesis._tenant.tenant_service.CacheProvider')
@patch('blocks_genesis._tenant.tenant_service.AsyncIOMotorClient')
async def test_get_tenant(mock_motor, mock_cache_provider, mock_get_secret, mock_load_tenant):
    mock_get_secret.return_value.DatabaseConnectionString = 'conn'
    mock_get_secret.return_value.RootDatabaseName = 'rootdb'
    mock_cache_provider.get_client.return_value = MagicMock()
    service = tenant_service.TenantService()
    # Not found in cache, triggers db load
    mock_load_tenant.return_value = MagicMock(tenant_id='tid')
    tenant = await service.get_tenant('tid')
    assert tenant is not None
    # Found in cache
    tenant2 = await service.get_tenant('tid')
    assert tenant2 is not None

@pytest.mark.asyncio
@patch('blocks_genesis._tenant.tenant_service.get_blocks_secret')
@patch('blocks_genesis._tenant.tenant_service.CacheProvider')
@patch('blocks_genesis._tenant.tenant_service.AsyncIOMotorClient')
async def test_get_tenant_by_domain(mock_motor, mock_cache_provider, mock_get_secret):
    mock_get_secret.return_value.DatabaseConnectionString = 'conn'
    mock_get_secret.return_value.RootDatabaseName = 'rootdb'
    mock_cache_provider.get_client.return_value = MagicMock()
    service = tenant_service.TenantService()
    mock_db = MagicMock()
    mock_db.__getitem__.return_value.find_one = AsyncMock(return_value={'TenantId': 'tid', 'JwtTokenParameters': {}})
    service.database = mock_db
    tenant = await service.get_tenant_by_domain('domain')
    assert tenant is not None

@pytest.mark.asyncio
@patch('blocks_genesis._tenant.tenant_service.TenantService.get_tenant', new_callable=AsyncMock)
async def test_get_db_connection(mock_get_tenant):
    service = tenant_service.TenantService.__new__(tenant_service.TenantService)
    mock_get_tenant.return_value = MagicMock(db_name='db', db_connection_string='conn')
    service.get_tenant = mock_get_tenant
    db, conn = await service.get_db_connection('tid')
    assert db == 'db'
    assert conn == 'conn'

@pytest.mark.asyncio
def test__load_tenants():
    service = tenant_service.TenantService.__new__(tenant_service.TenantService)
    mock_db = MagicMock()
    mock_cursor = AsyncMock()
    mock_cursor.__aiter__.return_value = [{"TenantId": "tid", "JwtTokenParameters": {}}]
    mock_db.__getitem__.return_value.find.return_value = mock_cursor
    service.database = mock_db
    service._tenant_cache = {}
    import asyncio
    async def run():
        await service._load_tenants()
        assert 'tid' in service._tenant_cache
    asyncio.run(run())

@pytest.mark.asyncio
def test__load_tenant_from_db():
    service = tenant_service.TenantService.__new__(tenant_service.TenantService)
    mock_db = MagicMock()
    mock_db.__getitem__.return_value.find_one = AsyncMock(return_value={'TenantId': 'tid', 'JwtTokenParameters': {}})
    service.database = mock_db
    import asyncio
    async def run():
        tenant = await service._load_tenant_from_db('tid')
        assert tenant is not None
    asyncio.run(run())

@pytest.mark.asyncio
def test__subscribe_to_updates():
    service = tenant_service.TenantService.__new__(tenant_service.TenantService)
    mock_cache = MagicMock()
    mock_cache.subscribe_async = AsyncMock()
    service.cache = mock_cache
    service._update_channel = 'chan'
    service._handle_update = AsyncMock()
    import asyncio
    async def run():
        await service._subscribe_to_updates()
        mock_cache.subscribe_async.assert_awaited()
    asyncio.run(run())

@pytest.mark.asyncio
def test__handle_update():
    service = tenant_service.TenantService.__new__(tenant_service.TenantService)
    service._load_tenants = AsyncMock()
    import asyncio
    async def run():
        await service._handle_update('chan', 'msg')
        service._load_tenants.assert_awaited()
    asyncio.run(run())

def test_get_tenant_service_and_initialize(monkeypatch):
    # Reset global
    tenant_service._tenant_service = None
    with pytest.raises(RuntimeError):
        tenant_service.get_tenant_service()
    class DummyService:
        async def initialize(self):
            pass
    monkeypatch.setattr(tenant_service, 'TenantService', DummyService)
    import asyncio
    asyncio.run(tenant_service.initialize_tenant_service()) 