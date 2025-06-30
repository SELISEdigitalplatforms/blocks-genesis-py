import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._database.mongo_context import MongoDbContextProvider

@pytest.mark.asyncio
@patch('blocks_genesis._database.mongo_context.get_tenant_service')
@patch('blocks_genesis._database.mongo_context.register')
@patch('blocks_genesis._database.mongo_context.MongoEventSubscriber')
async def test_get_database_and_collection(mock_sub, mock_register, mock_get_tenant_service):
    provider = MongoDbContextProvider()
    mock_tenant_service = mock_get_tenant_service.return_value
    mock_tenant_service.get_db_connection = AsyncMock(return_value=('db', 'conn'))
    with patch('blocks_genesis._database.mongo_context.MongoClient') as mock_client:
        mock_db = MagicMock()
        mock_client.return_value.__getitem__.return_value = mock_db
        db = await provider.get_database('tid')
        assert db is not None
        col = await provider.get_collection('col', 'tid')
        assert col is not None

@patch('blocks_genesis._database.mongo_context.get_tenant_service')
@patch('blocks_genesis._database.mongo_context.register')
@patch('blocks_genesis._database.mongo_context.MongoEventSubscriber')
def test_get_database_by_connection(mock_sub, mock_register, mock_get_tenant_service):
    provider = MongoDbContextProvider()
    with patch('blocks_genesis._database.mongo_context.MongoClient') as mock_client:
        mock_db = MagicMock()
        mock_client.return_value.__getitem__.return_value = mock_db
        db = provider.get_database_by_connection('conn', 'db')
        assert db is not None

@pytest.mark.asyncio
@patch('blocks_genesis._database.mongo_context.get_tenant_service')
@patch('blocks_genesis._database.mongo_context.register')
@patch('blocks_genesis._database.mongo_context.MongoEventSubscriber')
async def test_get_database_missing_tenant_id(mock_sub, mock_register, mock_get_tenant_service):
    provider = MongoDbContextProvider()
    mock_get_tenant_service.return_value.get_db_connection = AsyncMock(return_value=(None, None))
    db = await provider.get_database(None)
    assert db is None

@patch('blocks_genesis._database.mongo_context.get_tenant_service')
@patch('blocks_genesis._database.mongo_context.register')
@patch('blocks_genesis._database.mongo_context.MongoEventSubscriber')
def test_get_database_by_connection_errors(mock_sub, mock_register, mock_get_tenant_service):
    provider = MongoDbContextProvider()
    with pytest.raises(ValueError):
        provider.get_database_by_connection('', 'db')
    with pytest.raises(ValueError):
        provider.get_database_by_connection('conn', '') 