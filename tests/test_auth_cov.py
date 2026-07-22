import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from blocks_genesis._auth import auth

AU = 'blocks_genesis._auth.auth.'


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_extract_token_bearer(mock_bcm):
    mock_bcm.resolve_application_domain.return_value = 'd.com'
    req = MagicMock(); req.headers.get.return_value = 'Bearer abc123'
    assert await auth.extract_token_from_request(req, MagicMock()) == ('abc123', False, 'd.com')


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_extract_token_no_bearer_to_cookie(mock_bcm):
    mock_bcm.get_context.return_value = None
    req = MagicMock(); req.headers.get.return_value = ''
    assert await auth.extract_token_from_request(req, MagicMock()) == (None, False, None)


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_no_context(mock_bcm):
    mock_bcm.get_context.return_value = None
    assert await auth._extract_token_from_cookie(MagicMock(), MagicMock()) == (None, False, None)


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_localhost_fallback(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'localhost'
    mock_bcm.is_localhost_host.return_value = True
    req = MagicMock(); req.cookies.get.side_effect = lambda h: 'tok' if h == '127.0.0.1' else None
    assert await auth._extract_token_from_cookie(req, MagicMock()) == ('tok', False, 'localhost')


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_tenant_specific(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'app.com'
    mock_bcm.is_localhost_host.return_value = False
    req = MagicMock(); req.cookies.get.side_effect = lambda h: 'ctok' if h == 'app.com' else None
    assert await auth._extract_token_from_cookie(req, MagicMock()) == ('ctok', False, 'app.com')


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_third_party(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'app.com'
    mock_bcm.is_localhost_host.return_value = False
    req = MagicMock(); req.cookies.get.side_effect = lambda h: 'tp' if h == 'ckey' else None
    ts = MagicMock(); tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.cookie_key = 'ckey'
    ts.get_tenant = AsyncMock(return_value=tenant)
    assert await auth._extract_token_from_cookie(req, ts) == ('tp', True, 'app.com')


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_no_tenant(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'app.com'
    mock_bcm.is_localhost_host.return_value = False
    req = MagicMock(); req.cookies.get.return_value = None
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=None)
    assert await auth._extract_token_from_cookie(req, ts) == (None, False, None)


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_no_cookie_key(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'app.com'
    mock_bcm.is_localhost_host.return_value = False
    req = MagicMock(); req.cookies.get.return_value = None
    ts = MagicMock(); tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.cookie_key = ''
    ts.get_tenant = AsyncMock(return_value=tenant)
    assert await auth._extract_token_from_cookie(req, ts) == (None, False, None)


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_third_party_token_missing(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'app.com'
    mock_bcm.is_localhost_host.return_value = False
    req = MagicMock(); req.cookies.get.return_value = None
    ts = MagicMock(); tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.cookie_key = 'ckey'
    ts.get_tenant = AsyncMock(return_value=tenant)
    assert await auth._extract_token_from_cookie(req, ts) == (None, False, None)


@pytest.mark.asyncio
async def test_fetch_cert_bytes_file_success(tmp_path):
    f = tmp_path / 'cert.pfx'; f.write_bytes(b'data')
    assert await auth.fetch_cert_bytes(str(f)) == b'data'


@pytest.mark.asyncio
async def test_fetch_cert_bytes_file_error():
    with pytest.raises(RuntimeError):
        await auth.fetch_cert_bytes('/no/such/cert/file.pfx')


@pytest.mark.asyncio
async def test_resolve_signing_tenant_decode_fails():
    tenant = MagicMock()
    with patch(AU + 'jwt') as mj:
        mj.decode.side_effect = Exception('bad')
        assert await auth._resolve_signing_tenant('t', tenant, 't1', MagicMock()) is tenant


@pytest.mark.asyncio
async def test_resolve_signing_tenant_impersonated():
    tenant = MagicMock(); signer = MagicMock()
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=signer)
    with patch(AU + 'jwt') as mj:
        mj.decode.return_value = {'impersonated': True, 'original_tenant_id': 'orig'}
        assert await auth._resolve_signing_tenant('t', tenant, 't1', ts) is signer


@pytest.mark.asyncio
async def test_resolve_signing_tenant_not_impersonated():
    tenant = MagicMock()
    with patch(AU + 'jwt') as mj:
        mj.decode.return_value = {'impersonated': False}
        assert await auth._resolve_signing_tenant('t', tenant, 't1', MagicMock()) is tenant


@pytest.mark.asyncio
async def test_resolve_signing_tenant_same_id():
    tenant = MagicMock()
    with patch(AU + 'jwt') as mj:
        mj.decode.return_value = {'impersonated': True, 'original_tenant_id': 't1'}
        assert await auth._resolve_signing_tenant('t', tenant, 't1', MagicMock()) is tenant


@pytest.mark.asyncio
async def test_resolve_signing_tenant_signer_none():
    tenant = MagicMock()
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=None)
    with patch(AU + 'jwt') as mj:
        mj.decode.return_value = {'impersonated': True, 'original_tenant_id': 'orig'}
        assert await auth._resolve_signing_tenant('t', tenant, 't1', ts) is tenant


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_extract_token_bearer_empty(mock_bcm):
    mock_bcm.get_context.return_value = None
    req = MagicMock(); req.headers.get.return_value = 'Bearer '
    assert await auth.extract_token_from_request(req, MagicMock()) == (None, False, None)


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_localhost_no_match_falls_through(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = 'localhost'
    mock_bcm.is_localhost_host.return_value = True
    req = MagicMock(); req.cookies.get.return_value = None
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=None)
    assert await auth._extract_token_from_cookie(req, ts) == (None, False, None)
