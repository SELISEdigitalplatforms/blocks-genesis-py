import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from blocks_genesis._core import api

A = 'blocks_genesis._core.api.'
LIFESPAN_PATCHES = dict(
    SecretLoader=MagicMock(), configure_logger=MagicMock(), configure_tracing=MagicMock(),
    CacheProvider=MagicMock(), RedisClient=MagicMock(), DbContext=MagicMock(),
    MongoDbContextProvider=MagicMock(), get_blocks_secret=MagicMock(),
    RabbitMessageClient=MagicMock(), AzureMessageClient=MagicMock(),
    initialize_tenant_service=AsyncMock(),
)


@pytest.mark.asyncio
async def test_configure_lifespan_no_message_config():
    m = dict(LIFESPAN_PATCHES)
    m['SecretLoader'] = MagicMock(); m['SecretLoader'].return_value.load_secrets = AsyncMock()
    with patch.multiple('blocks_genesis._core.api', **m):
        await api.configure_lifespan('svc', None)


@pytest.mark.asyncio
async def test_configure_lifespan_no_bus():
    m = dict(LIFESPAN_PATCHES)
    m['SecretLoader'] = MagicMock(); m['SecretLoader'].return_value.load_secrets = AsyncMock()
    m['get_blocks_secret'] = MagicMock()
    with patch.multiple('blocks_genesis._core.api', **m):
        cfg = MagicMock(); cfg.connection = 'c'
        cfg.rabbit_mq_configuration = None
        cfg.azure_service_bus_configuration = None
        await api.configure_lifespan('svc', cfg)


def test_custom_generate_unique_id():
    route = MagicMock(); route.name = 'n'; route.path = '/a/b'
    assert api.custom_generate_unique_id(route) == 'n-_a_b'


def test_fast_api_app_returns_app():
    assert isinstance(api.fast_api_app(None), FastAPI)


@pytest.mark.asyncio
@patch(A + 'MongoHandler')
@patch(A + 'AzureMessageClient')
@patch(A + 'RabbitMessageClient')
async def test_close_lifespan_azure_runtimeerror_no_mongo(mock_rabbit, mock_azure, mock_mh):
    mock_rabbit.get_instance.return_value.close = AsyncMock()
    mock_azure.get_instance.return_value.close = AsyncMock(side_effect=RuntimeError())
    mock_mh._mongo_logger = None
    await api.close_lifespan()


@patch(A + 'FastAPIInstrumentor')
def test_configure_genesis_static_missing_dir(mock_instr):
    with pytest.raises(FileNotFoundError):
        api.configure_genesis(FastAPI(), serve_static=True, static_dir='/no/such/dir/xyz123')


@patch(A + 'StaticFiles')
@patch(A + 'FastAPIInstrumentor')
def test_configure_genesis_static_valid_dir(mock_instr, mock_sf, tmp_path):
    api.configure_genesis(FastAPI(), serve_static=True, static_dir=str(tmp_path))


@patch(A + 'StaticFiles')
@patch(A + 'FastAPIInstrumentor')
def test_configure_genesis_static_cwd(mock_instr, mock_sf, tmp_path, monkeypatch):
    (tmp_path / 'static').mkdir()
    monkeypatch.chdir(tmp_path)
    api.configure_genesis(FastAPI(), serve_static=True, static_dir='')


@patch(A + 'FastAPIInstrumentor')
def test_endpoints_show_docs(mock_instr):
    app = FastAPI()
    api.configure_genesis(app, show_docs=True)
    with TestClient(app) as client:
        assert client.get('/ping').json()['status'] == 'healthy'
        assert client.get('/swagger/index.html').status_code == 200
        assert client.get('/openapi.json').status_code == 200


@patch(A + 'FastAPIInstrumentor')
def test_endpoints_no_docs(mock_instr):
    app = FastAPI()
    api.configure_genesis(app, show_docs=False)
    with TestClient(app) as client:
        assert 'NOT_ALLOWED' in client.get('/swagger/index.html').text


def _find_openapi_endpoint(app):
    for r in app.routes:
        if getattr(r, 'path', '') == '/openapi.json' and getattr(getattr(r, 'endpoint', None), '__module__', '') == 'blocks_genesis._core.api':
            return r.endpoint
    raise AssertionError('custom openapi endpoint not found')


@pytest.mark.asyncio
@patch(A + 'FastAPIInstrumentor')
async def test_custom_openapi_endpoint_show_docs(mock_instr):
    app = FastAPI()
    api.configure_genesis(app, show_docs=True)
    result = await _find_openapi_endpoint(app)()
    assert result


@pytest.mark.asyncio
@patch(A + 'FastAPIInstrumentor')
async def test_custom_openapi_endpoint_no_docs(mock_instr):
    app = FastAPI()
    api.configure_genesis(app, show_docs=False)
    assert await _find_openapi_endpoint(app)() == {}
