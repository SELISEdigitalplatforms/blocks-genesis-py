import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.requests import Request
from blocks_genesis._middlewares.tenant_middleware import (
    TenantValidationMiddleware, is_sensitive_key, sanitize_dict,
)

T = 'blocks_genesis._middlewares.tenant_middleware.'


def _req(path='/api/x', headers=None, host='host'):
    r = MagicMock(spec=Request)
    r.url.path = path
    r.headers = headers if headers is not None else {}
    r.query_params = {}
    r.base_url.hostname = host
    return r


async def _agen(*chunks):
    for c in chunks:
        yield c


@pytest.mark.asyncio
@patch(T + 'get_tenant_service')
@patch(T + 'Activity')
@patch(T + 'BlocksContextManager')
async def test_dispatch_happy_path(mock_ctx, mock_activity, mock_gts):
    mock_ctx.is_localhost_host.return_value = False
    mock_ctx.resolve_application_domain.return_value = 'app'
    mock_ctx.create.return_value = MagicMock()
    mw = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    request = _req(headers={'x-blocks-key': 'key'})
    tenant = MagicMock(is_disabled=False, is_root_tenant=True, tenant_id='tid', applications=[])
    mock_gts.return_value.get_tenant = AsyncMock(return_value=tenant)
    mw._is_valid_origin_or_referer = MagicMock(return_value=True)
    response = MagicMock(status_code=200, headers={'content-type': 'application/json'})
    response.body_iterator = _agen(b'chunk1', b'chunk2')
    result = await mw.dispatch(request, AsyncMock(return_value=response))
    chunks = [c async for c in result.body_iterator]
    assert chunks == [b'chunk1', b'chunk2']


@pytest.mark.asyncio
@patch(T + 'get_tenant_service')
@patch(T + 'Activity')
@patch(T + 'BlocksContextManager')
async def test_dispatch_non_2xx_response(mock_ctx, mock_activity, mock_gts):
    mock_ctx.is_localhost_host.return_value = False
    mock_ctx.resolve_application_domain.return_value = 'app'
    mock_ctx.create.return_value = MagicMock()
    mw = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    request = _req(headers={'x-blocks-key': 'key'})
    tenant = MagicMock(is_disabled=False, is_root_tenant=False, tenant_id='tid', applications=[])
    mock_gts.return_value.get_tenant = AsyncMock(return_value=tenant)
    mw._is_valid_origin_or_referer = MagicMock(return_value=True)
    response = MagicMock(status_code=500, headers={})
    response.body_iterator = None
    result = await mw.dispatch(request, AsyncMock(return_value=response))
    assert result.status_code == 500


@pytest.mark.asyncio
@patch(T + 'get_tenant_service')
@patch(T + 'Activity')
@patch(T + 'BlocksContextManager')
async def test_dispatch_localhost_reject(mock_ctx, mock_activity, mock_gts):
    mock_ctx.is_localhost_host.return_value = True
    mw = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    request = _req(headers={}, host='localhost')
    result = await mw.dispatch(request, AsyncMock())
    assert result.status_code == 400


@pytest.mark.asyncio
@patch(T + 'get_tenant_service')
@patch(T + 'Activity')
@patch(T + 'BlocksContextManager')
async def test_dispatch_disabled_tenant(mock_ctx, mock_activity, mock_gts):
    mw = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    request = _req(headers={'x-blocks-key': 'key'})
    mock_gts.return_value.get_tenant = AsyncMock(return_value=MagicMock(is_disabled=True))
    result = await mw.dispatch(request, AsyncMock())
    assert result.status_code == 404


@pytest.mark.asyncio
@patch(T + 'get_tenant_service')
@patch(T + 'Activity')
@patch(T + 'BlocksContextManager')
async def test_dispatch_domain_path_success(mock_ctx, mock_activity, mock_gts):
    mock_ctx.is_localhost_host.return_value = False
    mock_ctx.resolve_application_domain.return_value = 'app'
    mock_ctx.create.return_value = MagicMock()
    mw = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    request = _req(headers={})
    tenant = MagicMock(is_disabled=False, is_root_tenant=True, tenant_id='tid', applications=[])
    mock_gts.return_value.get_tenant_by_domain = AsyncMock(return_value=tenant)
    mw._is_valid_origin_or_referer = MagicMock(return_value=True)
    response = MagicMock(status_code=204, headers={})
    response.body_iterator = None
    result = await mw.dispatch(request, AsyncMock(return_value=response))
    assert result.status_code == 204


def test_is_sensitive_key_and_sanitize():
    assert is_sensitive_key('') is False
    assert is_sensitive_key('my_token') is True
    assert sanitize_dict({'password': 'p', 'ok': 'v'}) == {'password': '[REDACTED]', 'ok': 'v'}


def test_is_valid_origin_no_current():
    mw = TenantValidationMiddleware(MagicMock())
    request = MagicMock()
    request.headers.get.return_value = None
    assert mw._is_valid_origin_or_referer(request, MagicMock(applications=[])) is True


@patch(T + 'BlocksContextManager')
def test_is_valid_origin_normalize_fallback(mock_ctx):
    mock_ctx.is_localhost_host.return_value = False
    mock_ctx.normalize_domain.return_value = 'nd'
    mw = TenantValidationMiddleware(MagicMock())
    request = MagicMock()
    request.headers.get.side_effect = lambda k: '/path' if k == 'origin' else None
    app = MagicMock(); app.domain = 'x'
    assert mw._is_valid_origin_or_referer(request, MagicMock(applications=[app])) is False


@pytest.mark.asyncio
@patch(T + 'get_tenant_service')
@patch(T + 'Activity')
@patch(T + 'BlocksContextManager')
async def test_dispatch_inner_exception_reraises(mock_ctx, mock_activity, mock_gts):
    mw = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    request = _req(headers={'x-blocks-key': 'key'})
    mock_gts.return_value.get_tenant = AsyncMock(side_effect=RuntimeError('boom'))
    with pytest.raises(RuntimeError):
        await mw.dispatch(request, AsyncMock())


@patch(T + 'BlocksContextManager')
def test_is_valid_origin_urlparse_exception(mock_ctx):
    mock_ctx.is_localhost_host.return_value = False
    mock_ctx.normalize_domain.return_value = 'nd'
    mw = TenantValidationMiddleware(MagicMock())
    request = MagicMock()
    request.headers.get.side_effect = lambda k: 'http://[::1' if k == 'origin' else None
    assert mw._is_valid_origin_or_referer(request, MagicMock(applications=[])) is False
