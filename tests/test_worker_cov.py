import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from blocks_genesis._core.worker import WorkerConsoleApp

MOD = 'blocks_genesis._core.worker'


def _mocks():
    m = dict(
        SecretLoader=MagicMock(), configure_logger=MagicMock(), configure_tracing=MagicMock(),
        CacheProvider=MagicMock(), RedisClient=MagicMock(), DbContext=MagicMock(),
        MongoDbContextProvider=MagicMock(), EventRegistry=MagicMock(), get_blocks_secret=MagicMock(),
        ConfigAzureServiceBus=MagicMock(), AzureMessageClient=MagicMock(), AzureMessageWorker=MagicMock(),
        RabbitMessageClient=MagicMock(), RabbitMessageWorker=MagicMock(),
        initialize_tenant_service=AsyncMock(),
    )
    m['SecretLoader'].return_value.load_secrets = AsyncMock()
    m['get_blocks_secret'].return_value.MessageConnectionString = 'conn'
    m['EventRegistry']._handlers = {}
    return m


@pytest.mark.asyncio
async def test_setup_services_no_message_bus():
    with patch.multiple(MOD, **_mocks()):
        cfg = MagicMock()
        cfg.connection = None
        cfg.rabbit_mq_configuration = None
        cfg.azure_service_bus_configuration = None
        app = WorkerConsoleApp('t', cfg, {})
        async with app.setup_services() as worker:
            assert worker is None


@pytest.mark.asyncio
async def test_setup_services_invalid_handler():
    with patch.multiple(MOD, **_mocks()):
        cfg = MagicMock()
        cfg.connection = 'c'
        cfg.rabbit_mq_configuration = None
        cfg.azure_service_bus_configuration = None
        app = WorkerConsoleApp('t', cfg, {123: lambda: None})
        async with app.setup_services() as worker:
            assert worker is None


@pytest.mark.asyncio
async def test_setup_services_exception_propagates():
    m = _mocks()
    m['SecretLoader'].return_value.load_secrets = AsyncMock(side_effect=RuntimeError('boom'))
    with patch.multiple(MOD, **m):
        app = WorkerConsoleApp('t', MagicMock(), {})
        with pytest.raises(RuntimeError):
            async with app.setup_services():
                pass


@pytest.mark.asyncio
async def test_cleanup_stops_mongo_logger():
    app = WorkerConsoleApp('t', MagicMock(), {})
    app.message_worker = None
    with patch(MOD + '.MongoHandler') as mock_mh:
        mock_mh._mongo_logger = MagicMock()
        await app.cleanup()
        mock_mh._mongo_logger.stop.assert_called()


@pytest.mark.asyncio
@patch(MOD + '.WorkerConsoleApp.setup_services')
async def test_run_without_callback(mock_ss):
    app = WorkerConsoleApp('t', MagicMock())
    worker = MagicMock(); worker.run = AsyncMock()
    mock_ss.return_value.__aenter__.return_value = worker
    mock_ss.return_value.__aexit__ = AsyncMock(return_value=False)
    await app.run(None)
    worker.run.assert_awaited()


@pytest.mark.asyncio
@patch(MOD + '.WorkerConsoleApp.setup_services')
async def test_run_cancelled_error(mock_ss):
    app = WorkerConsoleApp('t', MagicMock())
    worker = MagicMock()
    async def _cancel():
        raise asyncio.CancelledError()
    worker.run = _cancel
    mock_ss.return_value.__aenter__.return_value = worker
    mock_ss.return_value.__aexit__ = AsyncMock(return_value=False)
    await app.run(AsyncMock())
