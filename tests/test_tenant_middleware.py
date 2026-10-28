import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._middlewares.tenant_middleware import TenantValidationMiddleware
from fastapi import Request

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.tenant_middleware.get_tenant_service')
@patch('blocks_genesis._middlewares.tenant_middleware.Activity')
@patch('blocks_genesis._middlewares.tenant_middleware.BlocksContextManager')
async def test_dispatch_excluded_paths(mock_ctx_mgr, mock_activity, mock_get_tenant_service):
    middleware = TenantValidationMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    request.url.path = '/docs'
    call_next = AsyncMock(return_value='response')
    result = await middleware.dispatch(request, call_next)
    assert result == 'response'

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.tenant_middleware.get_tenant_service')
@patch('blocks_genesis._middlewares.tenant_middleware.Activity')
@patch('blocks_genesis._middlewares.tenant_middleware.BlocksContextManager')
async def test_dispatch_missing_tenant(mock_ctx_mgr, mock_activity, mock_get_tenant_service):
    middleware = TenantValidationMiddleware(MagicMock(), included_paths=['/not-excluded'])
    mock_ctx_mgr.is_localhost_host.return_value = False
    request = MagicMock(spec=Request)
    request.url.path = '/not-excluded'
    request.headers = {}
    request.query_params.get.return_value = None
    request.base_url.hostname = 'host'
    tenant_service = mock_get_tenant_service.return_value
    tenant_service.get_tenant_by_domain = AsyncMock(return_value=None)
    call_next = AsyncMock()
    result = await middleware.dispatch(request, call_next)
    assert result.status_code == 404

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.tenant_middleware.get_tenant_service')
@patch('blocks_genesis._middlewares.tenant_middleware.Activity')
@patch('blocks_genesis._middlewares.tenant_middleware.BlocksContextManager')
async def test_dispatch_valid_tenant(mock_ctx_mgr, mock_activity, mock_get_tenant_service):
    middleware = TenantValidationMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    request.url.path = '/not-excluded'
    request.headers.get.return_value = 'api-key'
    request.query_params.get.return_value = None
    tenant = MagicMock()
    tenant.is_disabled = False
    tenant.is_root_tenant = True
    tenant.tenant_id = 'tid'
    tenant.allowed_domains = []
    tenant.application_domain = 'host'
    tenant_service = mock_get_tenant_service.return_value
    tenant_service.get_tenant = AsyncMock(return_value=tenant)
    call_next = AsyncMock(return_value=MagicMock(status_code=200, headers={}, __class__=MagicMock()))
    result = await middleware.dispatch(request, call_next)
    assert hasattr(result, 'status_code')

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.tenant_middleware.get_tenant_service')
@patch('blocks_genesis._middlewares.tenant_middleware.Activity')
@patch('blocks_genesis._middlewares.tenant_middleware.BlocksContextManager')
async def test_dispatch_invalid_origin(mock_ctx_mgr, mock_activity, mock_get_tenant_service):
    middleware = TenantValidationMiddleware(MagicMock(), included_paths=['/not-excluded'])
    request = MagicMock(spec=Request)
    request.url.path = '/not-excluded'
    request.headers = {'x-blocks-key': 'api-key'}
    request.query_params.get.return_value = None
    tenant = MagicMock()
    tenant.is_disabled = False
    tenant.is_root_tenant = True
    tenant.tenant_id = 'tid'
    tenant.allowed_domains = []
    tenant.application_domain = 'host'
    tenant_service = mock_get_tenant_service.return_value
    tenant_service.get_tenant = AsyncMock(return_value=tenant)
    # Patch _is_valid_origin_or_referer to return False
    middleware._is_valid_origin_or_referer = MagicMock(return_value=False)
    call_next = AsyncMock()
    result = await middleware.dispatch(request, call_next)
    # The same answer as an unknown tenant, so the response does not reveal that the key exists.
    assert result.status_code == 404
    call_next.assert_not_awaited()

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.tenant_middleware.get_tenant_service')
@patch('blocks_genesis._middlewares.tenant_middleware.Activity')
@patch('blocks_genesis._middlewares.tenant_middleware.BlocksContextManager')
async def test_refused_origin_looks_like_an_unknown_tenant(mock_ctx_mgr, mock_activity, mock_get_tenant_service, caplog):
    middleware = TenantValidationMiddleware(MagicMock(), included_paths=['/api'])
    middleware._is_valid_origin_or_referer = MagicMock(return_value=False)

    def _request(key):
        request = MagicMock(spec=Request)
        request.url.path = '/api/x'
        request.headers = {'x-blocks-key': key}
        request.query_params.get.return_value = None
        return request

    tenant = MagicMock(is_disabled=False, tenant_id='tid')
    tenant_service = mock_get_tenant_service.return_value
    tenant_service.get_tenant = AsyncMock(side_effect=lambda key: tenant if key == 'known' else None)

    with caplog.at_level('DEBUG', logger='blocks_genesis._middlewares.tenant_middleware'):
        refused = await middleware.dispatch(_request('known'), AsyncMock())
    unknown = await middleware.dispatch(_request('unknown'), AsyncMock())

    assert (refused.status_code, refused.body) == (unknown.status_code, unknown.body)
    assert any(r.levelname == 'DEBUG' and 'origin' in r.getMessage().lower() for r in caplog.records)

def test_reject():
    middleware = TenantValidationMiddleware(MagicMock())
    resp = middleware._reject(400, 'msg')
    assert resp.status_code == 400
    assert resp.body

def test_is_valid_origin_or_referer():
    middleware = TenantValidationMiddleware(MagicMock())
    request = MagicMock()
    tenant = MagicMock()
    _app = MagicMock(); _app.domain = 'http://a.com'
    tenant.applications = [_app]
    request.headers.get.side_effect = lambda k: 'http://a.com' if k == 'origin' else None
    assert middleware._is_valid_origin_or_referer(request, tenant) 