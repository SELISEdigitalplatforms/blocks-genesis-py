import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from blocks_genesis._core import api

@pytest.mark.asyncio
@patch('blocks_genesis._core.api.SecretLoader')
@patch('blocks_genesis._core.api.configure_logger')
@patch('blocks_genesis._core.api.configure_tracing')
@patch('blocks_genesis._core.api.CacheProvider')
@patch('blocks_genesis._core.api.RedisClient')
@patch('blocks_genesis._core.api.initialize_tenant_service', new_callable=AsyncMock)
@patch('blocks_genesis._core.api.DbContext')
@patch('blocks_genesis._core.api.MongoDbContextProvider')
@patch('blocks_genesis._core.api.AzureMessageClient')
@patch('blocks_genesis._core.api.MessageConfiguration')
async def test_configure_lifespan(mock_msg_config, mock_client, mock_mongo, mock_db, mock_init_tenant, mock_redis, mock_cache, mock_tracing, mock_logger, mock_secret_loader):
    msg_config = MagicMock()
    await api.configure_lifespan('svc', msg_config)

@pytest.mark.asyncio
@patch('blocks_genesis._core.api.AzureMessageClient')
@patch('blocks_genesis._core.api.MongoHandler')
async def test_close_lifespan(mock_mongo, mock_client):
    mock_client.get_instance.return_value.close = AsyncMock()
    mock_mongo._mongo_logger = MagicMock()
    await api.close_lifespan()
    mock_mongo._mongo_logger.stop.assert_called()

def test_configure_middlewares():
    app = FastAPI()
    api.configure_middlewares(app, is_local=True)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        resp = client.get('/ping')
        assert resp.status_code == 200
        assert resp.json()['status'] == 'healthy' 