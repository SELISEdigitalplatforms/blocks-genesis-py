import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._database.mongo_context import MongoDbContextProvider
from blocks_genesis._database import mongo_context as mc
from blocks_genesis._database.mongo_event_subscriber import MongoEventSubscriber

P = 'blocks_genesis._database.mongo_context.'


@pytest.mark.asyncio
@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
async def test_get_database_missing_conn_raises(mock_sub, mock_register, mock_gts):
    mc._db_cache.set({}); mc._client_cache.set({})
    provider = MongoDbContextProvider()
    mock_gts.return_value.get_db_connection = AsyncMock(return_value=(None, None))
    with pytest.raises(ValueError):
        await provider.get_database('tid')


@pytest.mark.asyncio
@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
async def test_get_database_conn_already_cached(mock_sub, mock_register, mock_gts):
    mc._db_cache.set({}); mc._client_cache.set({'conn': MagicMock()})
    provider = MongoDbContextProvider()
    mock_gts.return_value.get_db_connection = AsyncMock(return_value=('db', 'conn'))
    db = await provider.get_database('tid')
    assert db is not None


@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
def test_get_database_by_connection_cache_hit(mock_sub, mock_register, mock_gts):
    mc._db_cache.set({'db': MagicMock()})
    provider = MongoDbContextProvider()
    db = provider.get_database_by_connection('conn', 'db')
    assert db is not None


@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
def test_get_database_by_connection_creates_client(mock_sub, mock_register, mock_gts):
    mc._db_cache.set({}); mc._client_cache.set({})
    provider = MongoDbContextProvider()
    with patch(P + 'MongoClient') as mock_client:
        mock_client.return_value.__getitem__.return_value = MagicMock()
        db = provider.get_database_by_connection('conn', 'db')
        assert db is not None


@pytest.mark.asyncio
@patch(P + 'BlocksContextManager')
@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
async def test_get_collection_no_db_raises(mock_sub, mock_register, mock_gts, mock_ctx):
    mock_ctx.get_context.return_value = None
    provider = MongoDbContextProvider()
    with pytest.raises(RuntimeError):
        await provider.get_collection('col', None)


def test_event_subscriber_succeeded_failed_without_activity():
    sub = MongoEventSubscriber()
    event = MagicMock(); event.request_id = 'missing-rid'
    sub.succeeded(event)
    sub.failed(event)
    assert sub._activities == {}


@pytest.mark.asyncio
@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
async def test_get_database_creates_client(mock_sub, mock_register, mock_gts):
    mc._db_cache.set({}); mc._client_cache.set({})
    provider = MongoDbContextProvider()
    mock_gts.return_value.get_db_connection = AsyncMock(return_value=('db', 'conn'))
    with patch(P + 'MongoClient') as mock_client:
        mock_client.return_value.__getitem__.return_value = MagicMock()
        db = await provider.get_database('tid')
        assert db is not None


@patch(P + 'get_tenant_service')
@patch(P + 'register')
@patch(P + 'MongoEventSubscriber')
def test_get_database_by_connection_client_cached(mock_sub, mock_register, mock_gts):
    mc._db_cache.set({}); mc._client_cache.set({'conn': MagicMock()})
    provider = MongoDbContextProvider()
    db = provider.get_database_by_connection('conn', 'db')
    assert db is not None
