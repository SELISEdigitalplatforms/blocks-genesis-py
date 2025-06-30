import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request
from blocks_genesis._auth import auth
from datetime import datetime, timezone

@pytest.mark.asyncio
@patch('aiohttp.ClientSession')
async def test_fetch_cert_bytes_http(mock_session):
    mock_resp = AsyncMock()
    mock_resp.read.return_value = b'certdata'
    mock_resp.raise_for_status.return_value = None
    mock_session.return_value.__aenter__.return_value = mock_session.return_value
    mock_session.return_value.get.return_value.__aenter__.return_value = mock_resp
    result = await auth.fetch_cert_bytes('http://example.com/cert')
    assert result == b'certdata'

@pytest.mark.asyncio
@patch('builtins.open', new_callable=MagicMock)
@patch('asyncio.get_running_loop')
async def test_fetch_cert_bytes_file(mock_loop, mock_open):
    mock_file = MagicMock()
    mock_file.read.return_value = b'certdata'
    mock_open.return_value.__enter__.return_value = mock_file
    mock_loop.return_value.run_in_executor = AsyncMock(return_value=b'certdata')
    result = await auth.fetch_cert_bytes('file.cert')
    assert result == b'certdata'

@pytest.mark.asyncio
@patch('blocks_genesis._auth.auth.fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_cache_miss(mock_fetch):
    cache_client = MagicMock()
    cache_client.get_string_value.return_value = None
    cache_client.add_string_value = AsyncMock()
    tenant = MagicMock()
    tenant.jwt_token_parameters.public_certificate_path = 'certpath'
    tenant.jwt_token_parameters.issue_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    tenant.jwt_token_parameters.certificate_valid_for_number_of_days = 10
    tenant.jwt_token_parameters.public_certificate_password = 'pass'
    mock_fetch.return_value = b'certdata'
    result = await auth.get_tenant_cert(cache_client, tenant, 'tid')
    assert result == b'certdata'
    cache_client.add_string_value.assert_awaited()

@patch('cryptography.hazmat.primitives.serialization.pkcs12.load_pkcs12')
def test_create_certificate_success(mock_load):
    cert = MagicMock()
    cert.additional_certs = [MagicMock(certificate='certobj')]
    mock_load.return_value = cert
    result = auth.create_certificate(b'data', 'pass')
    assert result == 'certobj'

@patch('cryptography.hazmat.primitives.serialization.pkcs12.load_pkcs12', side_effect=Exception('fail'))
def test_create_certificate_fail(mock_load):
    result = auth.create_certificate(b'data', 'pass')
    assert result is None

@pytest.mark.asyncio
@patch('blocks_genesis._auth.auth.get_tenant_cert', new_callable=AsyncMock)
@patch('blocks_genesis._auth.auth.create_certificate')
@patch('jwt.decode')
@patch('blocks_genesis._auth.auth.BlocksContextManager')
@patch('blocks_genesis._auth.auth.Activity')
async def test_authenticate_success(mock_activity, mock_context_mgr, mock_jwt_decode, mock_create_cert, mock_get_cert):
    request = MagicMock()
    request.headers.get.return_value = 'Bearer token'
    request.cookies.get.return_value = ''
    tenant_service = MagicMock()
    tenant = MagicMock()
    tenant.jwt_token_parameters.public_certificate_password = 'pass'
    tenant.jwt_token_parameters.issuer = 'issuer'
    tenant.jwt_token_parameters.audiences = 'aud'
    mock_context_mgr.get_context.return_value = MagicMock(tenant_id='tid')
    tenant_service.get_tenant = AsyncMock(return_value=tenant)
    mock_get_cert.return_value = b'certdata'
    cert = MagicMock()
    cert.public_key.return_value.public_bytes.return_value = b'keypem'
    mock_create_cert.return_value = cert
    mock_jwt_decode.return_value = {'sub': 'user'}
    mock_context_mgr.create_from_jwt_claims.return_value = MagicMock(user_id='user', roles=['role'], permissions=['perm'])
    result = await auth.authenticate(request, tenant_service, MagicMock())
    assert 'sub' in result

@pytest.mark.asyncio
async def test_authenticate_missing_token():
    request = MagicMock()
    request.headers.get.return_value = None
    request.cookies.get.return_value = ''
    with pytest.raises(HTTPException) as exc:
        await auth.authenticate(request, MagicMock(), MagicMock())
    assert exc.value.status_code == 401

@patch('blocks_genesis._auth.auth.get_tenant_service')
@patch('blocks_genesis._auth.auth.CacheProvider.get_client')
@patch('blocks_genesis._auth.auth.DbContext.get_provider')
@patch('blocks_genesis._auth.auth.authenticate', new_callable=AsyncMock)
@patch('blocks_genesis._auth.auth.BlocksContextManager.get_context')
def test_authorize_bypass(mock_get_context, mock_auth, mock_db, mock_cache, mock_tenant):
    mock_get_context.return_value = MagicMock(roles=['role'], permissions=['perm'], service_name='svc', tenant_id='tid')
    dep = auth.authorize(bypass_authorization=True)
    # Should return a Depends object
    assert dep is not None 