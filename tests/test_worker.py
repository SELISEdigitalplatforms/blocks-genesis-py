import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from blocks_genesis._core.worker import WorkerConsoleApp

@pytest.mark.asyncio
@patch('blocks_genesis._core.worker.SecretLoader')
@patch('blocks_genesis._core.worker.configure_logger')
@patch('blocks_genesis._core.worker.configure_tracing')
@patch('blocks_genesis._core.worker.CacheProvider')
@patch('blocks_genesis._core.worker.RedisClient')
@patch('blocks_genesis._core.worker.initialize_tenant_service', new_callable=AsyncMock)
@patch('blocks_genesis._core.worker.DbContext')
@patch('blocks_genesis._core.worker.MongoDbContextProvider')
@patch('blocks_genesis._core.worker.EventRegistry')
@patch('blocks_genesis._core.worker.get_blocks_secret')
@patch('blocks_genesis._core.worker.ConfigAzureServiceBus')
@patch('blocks_genesis._core.worker.AzureMessageClient')
@patch('blocks_genesis._core.worker.AzureMessageWorker')
@patch('blocks_genesis._core.worker.MessageConfiguration')
async def test_setup_services_and_cleanup(
    mock_msg_config, mock_worker, mock_client, mock_config, mock_get_secret, mock_event_registry,
    mock_mongo_provider, mock_dbcontext, mock_init_tenant, mock_redis, mock_cache, mock_tracing, mock_logger, mock_secret_loader
):
    # Setup
    msg_config = MagicMock()
    msg_config.connection = None
    mock_msg_config.return_value = msg_config
    mock_get_secret.return_value.MessageConnectionString = 'connstr'
    mock_event_registry._handlers = {}
    mock_event_registry.register = lambda event_type: lambda handler: handler
    mock_worker_instance = MagicMock()
    mock_worker.return_value = mock_worker_instance
    mock_worker_instance.initialize.return_value = None
    mock_worker_instance.stop = AsyncMock()
    mock_secret_loader.return_value.load_secrets = AsyncMock()
    app = WorkerConsoleApp('test', msg_config, {'evt': lambda: None})
    # Test setup_services
    async with app.setup_services() as worker:
        assert worker is mock_worker_instance
    # Test cleanup
    await app.cleanup()

@pytest.mark.asyncio
@patch('blocks_genesis._core.worker.SecretLoader')
@patch('blocks_genesis._core.worker.configure_logger')
@patch('blocks_genesis._core.worker.configure_tracing')
@patch('blocks_genesis._core.worker.CacheProvider')
@patch('blocks_genesis._core.worker.RedisClient')
@patch('blocks_genesis._core.worker.initialize_tenant_service', new_callable=AsyncMock)
@patch('blocks_genesis._core.worker.DbContext')
@patch('blocks_genesis._core.worker.MongoDbContextProvider')
@patch('blocks_genesis._core.worker.EventRegistry')
@patch('blocks_genesis._core.worker.get_blocks_secret')
@patch('blocks_genesis._core.worker.ConfigAzureServiceBus')
@patch('blocks_genesis._core.worker.AzureMessageClient')
@patch('blocks_genesis._core.worker.AzureMessageWorker')
@patch('blocks_genesis._core.worker.MessageConfiguration')
async def test_setup_services_event_registration_error(
    mock_msg_config, mock_worker, mock_client, mock_config, mock_get_secret, mock_event_registry,
    mock_mongo_provider, mock_dbcontext, mock_init_tenant, mock_redis, mock_cache, mock_tracing, mock_logger, mock_secret_loader
):
    msg_config = MagicMock()
    msg_config.connection = None
    mock_msg_config.return_value = msg_config
    mock_get_secret.return_value.MessageConnectionString = 'connstr'
    mock_event_registry._handlers = {'evt': lambda: None}
    mock_event_registry.register = lambda event_type: lambda handler: handler
    mock_worker_instance = MagicMock()
    mock_worker.return_value = mock_worker_instance
    mock_worker_instance.initialize.return_value = None
    mock_worker_instance.stop = AsyncMock()
    mock_secret_loader.return_value.load_secrets = AsyncMock()
    app = WorkerConsoleApp('test', msg_config, {'evt': lambda: None})
    async with app.setup_services() as worker:
        assert worker is mock_worker_instance
    await app.cleanup()

@pytest.mark.asyncio
@patch('blocks_genesis._core.worker.WorkerConsoleApp.setup_services')
async def test_run(mock_setup_services):
    app = WorkerConsoleApp('test', MagicMock())
    mock_worker = MagicMock()
    mock_worker.run = AsyncMock()
    mock_setup_services.return_value.__aenter__.return_value = mock_worker
    await app.run()
    mock_worker.run.assert_awaited()

@pytest.mark.asyncio
@patch('blocks_genesis._core.worker.WorkerConsoleApp.setup_services')
async def test_run_keyboard_interrupt(mock_setup_services):
    app = WorkerConsoleApp('test', MagicMock())
    mock_worker = MagicMock()
    async def raise_interrupt():
        raise KeyboardInterrupt()
    mock_worker.run = raise_interrupt
    mock_setup_services.return_value.__aenter__.return_value = mock_worker
    await app.run() 