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


from datetime import datetime, timezone
from fastapi import HTTPException


@pytest.mark.asyncio
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
@patch(AU + 'CacheProvider')
async def test_authenticate_token_missing(mock_cp, mock_extract):
    mock_extract.return_value = (None, False, None)
    with pytest.raises(HTTPException) as e:
        await auth.authenticate(MagicMock(), MagicMock())
    assert e.value.status_code == 401


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
async def test_authenticate_tenant_not_found(mock_extract, mock_bcm):
    mock_extract.return_value = ('tok', False, 'app.com')
    mock_bcm.get_context.return_value = None
    req = MagicMock(); req.headers.get.return_value = 'tid'; req.query_params.get.return_value = None
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as e:
        await auth.authenticate(req, ts, cache_client=MagicMock())
    assert e.value.status_code == 401


@pytest.mark.asyncio
@patch(AU + 'Activity')
@patch(AU + 'BlocksContextManager')
@patch(AU + 'validate_with_fallback', new_callable=AsyncMock)
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
async def test_authenticate_third_party_success(mock_extract, mock_vwf, mock_bcm, mock_act):
    mock_extract.return_value = ('tok', True, 'app.com')
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    mock_vwf.return_value = {'sub': 'u1'}
    mock_bcm.create_from_jwt_claims.return_value = MagicMock(user_id='u1', tenant_id='tid')
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=MagicMock())
    assert await auth.authenticate(MagicMock(), ts, cache_client=MagicMock()) == {'sub': 'u1'}


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
@patch(AU + 'validate_with_fallback', new_callable=AsyncMock)
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
async def test_authenticate_third_party_fails(mock_extract, mock_vwf, mock_bcm):
    mock_extract.return_value = ('tok', True, 'app.com')
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    mock_vwf.return_value = None
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=MagicMock())
    with pytest.raises(HTTPException):
        await auth.authenticate(MagicMock(), ts, cache_client=MagicMock())


@pytest.mark.asyncio
@patch(AU + 'Activity')
@patch(AU + 'BlocksContextManager')
@patch(AU + '_resolve_signing_tenant', new_callable=AsyncMock)
@patch(AU + 'validate_jwt_token', new_callable=AsyncMock)
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
async def test_authenticate_tenant_token_primary_success(mock_extract, mock_vjt, mock_rst, mock_bcm, mock_act):
    mock_extract.return_value = ('tok', False, 'app.com')
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    mock_rst.return_value = MagicMock()
    mock_vjt.return_value = {'sub': 'u1'}
    mock_bcm.create_from_jwt_claims.return_value = MagicMock(user_id='u1', tenant_id='tid')
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=MagicMock())
    assert await auth.authenticate(MagicMock(), ts, cache_client=MagicMock()) == {'sub': 'u1'}


@pytest.mark.asyncio
@patch(AU + 'Activity')
@patch(AU + 'BlocksContextManager')
@patch(AU + '_resolve_signing_tenant', new_callable=AsyncMock)
@patch(AU + 'validate_with_fallback', new_callable=AsyncMock)
@patch(AU + 'validate_jwt_token', new_callable=AsyncMock)
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
async def test_authenticate_primary_fails_fallback_success(mock_extract, mock_vjt, mock_vwf, mock_rst, mock_bcm, mock_act):
    mock_extract.return_value = ('tok', False, 'app.com')
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    mock_rst.return_value = MagicMock()
    mock_vjt.side_effect = HTTPException(status_code=401)
    mock_vwf.return_value = {'sub': 'u1'}
    mock_bcm.create_from_jwt_claims.return_value = MagicMock(user_id='u1', tenant_id='tid')
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=MagicMock())
    assert await auth.authenticate(MagicMock(), ts, cache_client=MagicMock()) == {'sub': 'u1'}


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
@patch(AU + '_resolve_signing_tenant', new_callable=AsyncMock)
@patch(AU + 'validate_with_fallback', new_callable=AsyncMock)
@patch(AU + 'validate_jwt_token', new_callable=AsyncMock)
@patch(AU + 'extract_token_from_request', new_callable=AsyncMock)
async def test_authenticate_primary_and_fallback_fail(mock_extract, mock_vjt, mock_vwf, mock_rst, mock_bcm):
    mock_extract.return_value = ('tok', False, 'app.com')
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    mock_bcm.get_context.return_value = ctx
    mock_rst.return_value = MagicMock()
    mock_vjt.side_effect = HTTPException(status_code=401)
    mock_vwf.return_value = None
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=MagicMock())
    with pytest.raises(HTTPException):
        await auth.authenticate(MagicMock(), ts, cache_client=MagicMock())


def test_create_certificate_success():
    with patch(AU + 'pkcs12') as mp:
        cert = MagicMock(); ac = MagicMock(); ac.certificate = 'CERT'
        cert.additional_certs = [ac]
        mp.load_pkcs12.return_value = cert
        assert auth.create_certificate(b'data', 'pw') == 'CERT'


def test_create_certificate_no_additional():
    with patch(AU + 'pkcs12') as mp:
        cert = MagicMock(); cert.additional_certs = []
        mp.load_pkcs12.return_value = cert
        assert auth.create_certificate(b'data') is None


def test_create_certificate_exception():
    with patch(AU + 'pkcs12') as mp:
        mp.load_pkcs12.side_effect = Exception('bad')
        assert auth.create_certificate(b'data') is None


@pytest.mark.asyncio
async def test_get_tenant_cert_no_params():
    tenant = MagicMock(); tenant.jwt_token_parameters = None
    assert await auth.get_tenant_cert(MagicMock(), tenant, 'tid') is None


@pytest.mark.asyncio
async def test_get_tenant_cert_cache_hit():
    tenant = MagicMock()
    cache = MagicMock(); cache.get_bytes_value.return_value = b'cached'
    assert await auth.get_tenant_cert(cache, tenant, 'tid') == b'cached'


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_fetch_and_cache(mock_fetch):
    tenant = MagicMock()
    tenant.jwt_token_parameters.issue_date = datetime.now(timezone.utc)
    tenant.jwt_token_parameters.certificate_valid_for_number_of_days = 365
    cache = MagicMock()
    cache.get_bytes_value.side_effect = Exception('miss')
    cache.add_bytes_value_async = AsyncMock()
    mock_fetch.return_value = b'certbytes'
    assert await auth.get_tenant_cert(cache, tenant, 'tid') == b'certbytes'
    cache.add_bytes_value_async.assert_awaited()


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_fetch_none(mock_fetch):
    tenant = MagicMock()
    cache = MagicMock(); cache.get_bytes_value.return_value = None
    mock_fetch.return_value = None
    assert await auth.get_tenant_cert(cache, tenant, 'tid') is None


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_no_issue_date(mock_fetch):
    tenant = MagicMock()
    tenant.jwt_token_parameters.issue_date = None
    cache = MagicMock(); cache.get_bytes_value.return_value = None
    mock_fetch.return_value = b'cb'
    assert await auth.get_tenant_cert(cache, tenant, 'tid') == b'cb'


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_ttl_exception(mock_fetch):
    tenant = MagicMock()
    tenant.jwt_token_parameters.issue_date = 'bad-date'
    cache = MagicMock(); cache.get_bytes_value.return_value = None
    mock_fetch.return_value = b'cb'
    assert await auth.get_tenant_cert(cache, tenant, 'tid') == b'cb'


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_cookie_no_app_domain(mock_bcm):
    ctx = MagicMock(); ctx.tenant_id = 't1'
    mock_bcm.get_context.return_value = ctx
    mock_bcm.resolve_application_domain.return_value = None
    ts = MagicMock(); ts.get_tenant = AsyncMock(return_value=None)
    assert await auth._extract_token_from_cookie(MagicMock(), ts) == (None, False, None)


@pytest.mark.asyncio
async def test_validate_jwt_no_params():
    tenant = MagicMock(); tenant.jwt_token_parameters = None
    with pytest.raises(HTTPException):
        await auth.validate_jwt_token('t', tenant, MagicMock(), MagicMock())


@pytest.mark.asyncio
@patch(AU + 'get_tenant_cert', new_callable=AsyncMock)
async def test_validate_jwt_no_cert_bytes(mock_gtc):
    mock_gtc.return_value = None
    with pytest.raises(HTTPException):
        await auth.validate_jwt_token('t', MagicMock(), MagicMock(), MagicMock())


@pytest.mark.asyncio
@patch(AU + 'create_certificate')
@patch(AU + 'get_tenant_cert', new_callable=AsyncMock)
async def test_validate_jwt_no_cert(mock_gtc, mock_cc):
    mock_gtc.return_value = b'b'; mock_cc.return_value = None
    with pytest.raises(HTTPException):
        await auth.validate_jwt_token('t', MagicMock(), MagicMock(), MagicMock())


@pytest.mark.asyncio
@patch(AU + 'jwt')
@patch(AU + 'create_certificate')
@patch(AU + 'get_tenant_cert', new_callable=AsyncMock)
async def test_validate_jwt_success(mock_gtc, mock_cc, mock_jwt):
    mock_gtc.return_value = b'b'
    cert = MagicMock(); cert.public_key.return_value.public_bytes.return_value.decode.return_value = 'pem'
    mock_cc.return_value = cert
    mock_jwt.decode.return_value = {'sub': 'u1'}
    req = MagicMock(); req.url = 'http://x'
    result = await auth.validate_jwt_token('tok', MagicMock(), MagicMock(), req)
    assert result['sub'] == 'u1'


@pytest.mark.asyncio
@patch(AU + 'jwt')
@patch(AU + 'create_certificate')
@patch(AU + 'get_tenant_cert', new_callable=AsyncMock)
async def test_validate_jwt_expired(mock_gtc, mock_cc, mock_jwt):
    mock_gtc.return_value = b'b'
    cert = MagicMock(); cert.public_key.return_value.public_bytes.return_value.decode.return_value = 'pem'
    mock_cc.return_value = cert
    mock_jwt.decode.side_effect = auth.ExpiredSignatureError()
    with pytest.raises(HTTPException):
        await auth.validate_jwt_token('tok', MagicMock(), MagicMock(), MagicMock())


@pytest.mark.asyncio
@patch(AU + 'jwt')
@patch(AU + 'create_certificate')
@patch(AU + 'get_tenant_cert', new_callable=AsyncMock)
async def test_validate_jwt_invalid(mock_gtc, mock_cc, mock_jwt):
    mock_gtc.return_value = b'b'
    cert = MagicMock(); cert.public_key.return_value.public_bytes.return_value.decode.return_value = 'pem'
    mock_cc.return_value = cert
    mock_jwt.decode.side_effect = auth.InvalidTokenError('bad')
    with pytest.raises(auth.InvalidTokenError):
        await auth.validate_jwt_token('tok', MagicMock(), MagicMock(), MagicMock())


@pytest.mark.asyncio
@patch(AU + 'jwt')
@patch(AU + 'PyJWKClient')
async def test_validate_via_jwks_success(mock_pjc, mock_jwt):
    mock_pjc.return_value.get_signing_key_from_jwt.return_value.key = 'k'
    mock_jwt.decode.return_value = {'sub': 'u1'}
    assert await auth._validate_via_jwks('tok', 'http://j', 'iss', ['aud']) == {'sub': 'u1'}


@pytest.mark.asyncio
@patch(AU + 'PyJWKClient')
async def test_validate_via_jwks_exception(mock_pjc):
    mock_pjc.side_effect = Exception('bad')
    assert await auth._validate_via_jwks('tok', 'http://j', 'iss', ['aud']) is None


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_validate_via_public_cert_no_bytes(mock_fetch):
    mock_fetch.return_value = None
    assert await auth._validate_via_public_cert('t', '/p', None, 'iss', ['aud']) is None


@pytest.mark.asyncio
@patch(AU + 'create_certificate')
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_validate_via_public_cert_no_cert(mock_fetch, mock_cc):
    mock_fetch.return_value = b'b'; mock_cc.return_value = None
    assert await auth._validate_via_public_cert('t', '/p', None, 'iss', ['aud']) is None


@pytest.mark.asyncio
@patch(AU + 'jwt')
@patch(AU + 'create_certificate')
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_validate_via_public_cert_success(mock_fetch, mock_cc, mock_jwt):
    mock_fetch.return_value = b'b'
    cert = MagicMock(); cert.public_key.return_value.public_bytes.return_value = b'pem'
    mock_cc.return_value = cert
    mock_jwt.decode.return_value = {'sub': 'u1'}
    assert await auth._validate_via_public_cert('t', '/p', None, 'iss', ['aud']) == {'sub': 'u1'}


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_validate_via_public_cert_exception(mock_fetch):
    mock_fetch.side_effect = Exception('bad')
    assert await auth._validate_via_public_cert('t', '/p', None, 'iss', ['aud']) is None


@pytest.mark.asyncio
async def test_validate_with_fallback_no_params():
    tenant = MagicMock(); tenant.third_party_jwt_token_parameters = None
    assert await auth.validate_with_fallback('t', tenant, MagicMock()) is None


@pytest.mark.asyncio
@patch(AU + '_validate_via_jwks', new_callable=AsyncMock)
async def test_validate_with_fallback_jwks(mock_jwks):
    tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.jwks_url = 'http://j'
    mock_jwks.return_value = {'sub': 'u1'}
    req = MagicMock(); req.url = 'http://x'
    assert (await auth.validate_with_fallback('tok', tenant, req))['sub'] == 'u1'


@pytest.mark.asyncio
@patch(AU + '_validate_via_public_cert', new_callable=AsyncMock)
@patch(AU + '_validate_via_jwks', new_callable=AsyncMock)
async def test_validate_with_fallback_public_cert(mock_jwks, mock_pc):
    tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.jwks_url = 'http://j'
    tenant.third_party_jwt_token_parameters.public_certificate_path = '/p'
    mock_jwks.return_value = None
    mock_pc.return_value = {'sub': 'u2'}
    req = MagicMock(); req.url = 'http://x'
    assert (await auth.validate_with_fallback('tok', tenant, req))['sub'] == 'u2'


@pytest.mark.asyncio
@patch(AU + '_validate_via_public_cert', new_callable=AsyncMock)
@patch(AU + '_validate_via_jwks', new_callable=AsyncMock)
async def test_validate_with_fallback_all_fail(mock_jwks, mock_pc):
    tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.jwks_url = 'http://j'
    tenant.third_party_jwt_token_parameters.public_certificate_path = '/p'
    mock_jwks.return_value = None; mock_pc.return_value = None
    assert await auth.validate_with_fallback('tok', tenant, MagicMock()) is None


@pytest.mark.asyncio
async def test_check_standard_access_no_resource():
    assert await auth.check_standard_access(MagicMock(), '', MagicMock()) is False


@pytest.mark.asyncio
async def test_check_standard_access_no_context():
    assert await auth.check_standard_access(None, 'res', MagicMock()) is False
    ctx = MagicMock(); ctx.tenant_id = None
    assert await auth.check_standard_access(ctx, 'res', MagicMock()) is False


@pytest.mark.asyncio
@patch(AU + '_check_quota', new_callable=AsyncMock)
async def test_check_standard_access_quota_exceeded(mock_q):
    mock_q.return_value = False
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    assert await auth.check_standard_access(ctx, 'res', MagicMock()) is False


@pytest.mark.asyncio
@patch(AU + '_check_permission', new_callable=AsyncMock)
@patch(AU + '_check_quota', new_callable=AsyncMock)
async def test_check_standard_access_permission_ok(mock_q, mock_p):
    mock_q.return_value = True; mock_p.return_value = True
    ctx = MagicMock(); ctx.tenant_id = 'tid'; ctx.roles = ['r']; ctx.permissions = ['p']; ctx.impersonated = False
    assert await auth.check_standard_access(ctx, 'res', MagicMock()) is True


@pytest.mark.asyncio
@patch(AU + '_check_permission', new_callable=AsyncMock)
@patch(AU + '_check_quota', new_callable=AsyncMock)
async def test_check_standard_access_impersonated(mock_q, mock_p):
    mock_q.return_value = True; mock_p.return_value = False
    ctx = MagicMock(); ctx.tenant_id = 'tid'; ctx.roles = None; ctx.permissions = None
    ctx.impersonated = True; ctx.original_tenant_id = 'orig'
    assert await auth.check_standard_access(ctx, 'res', MagicMock()) is False


@pytest.mark.asyncio
async def test_check_quota_returns_true():
    ctx = MagicMock(); ctx.tenant_id = 'tid'
    assert await auth._check_quota(ctx, 'res', MagicMock()) is True


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_no_effective_tenant(mock_bcm):
    mock_bcm.get_context.return_value = None
    assert await auth._check_permission('res', ['r'], ['p'], None, MagicMock()) is False


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_no_roles_no_perms(mock_bcm):
    bc = MagicMock(); bc.impersonated = False; bc.tenant_id = 'tid'; bc.organization_id = 'org'
    mock_bcm.get_context.return_value = bc
    dbc = MagicMock(); dbc.get_collection = AsyncMock()
    assert await auth._check_permission('res', [], [], 'tid', dbc) is False


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_has_access(mock_bcm):
    bc = MagicMock(); bc.impersonated = False; bc.tenant_id = 'tid'; bc.organization_id = 'org'
    mock_bcm.get_context.return_value = bc
    coll = MagicMock(); coll.count_documents = AsyncMock(return_value=1)
    dbc = MagicMock(); dbc.get_collection = AsyncMock(return_value=coll)
    assert await auth._check_permission('res', ['r'], ['p'], 'tid', dbc) is True


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_denied_no_org(mock_bcm):
    bc = MagicMock(); bc.impersonated = False; bc.tenant_id = 'tid'; bc.organization_id = None
    mock_bcm.get_context.return_value = bc
    coll = MagicMock(); coll.count_documents = AsyncMock(return_value=0)
    dbc = MagicMock(); dbc.get_collection = AsyncMock(return_value=coll)
    assert await auth._check_permission('res', ['r'], [], 'tid', dbc) is False


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_impersonated(mock_bcm):
    bc = MagicMock(); bc.impersonated = True; bc.original_tenant_id = 'orig'; bc.organization_id = 'org'
    mock_bcm.get_context.return_value = bc
    coll = MagicMock(); coll.count_documents = AsyncMock(return_value=1)
    dbc = MagicMock(); dbc.get_collection = AsyncMock(return_value=coll)
    assert await auth._check_permission('res', [], ['p'], 'tid', dbc) is True


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_bc_none_uses_arg(mock_bcm):
    mock_bcm.get_context.return_value = None
    coll = MagicMock(); coll.count_documents = AsyncMock(return_value=1)
    dbc = MagicMock(); dbc.get_collection = AsyncMock(return_value=coll)
    assert await auth._check_permission('res', ['r'], ['p'], 'tid', dbc) is True


@pytest.mark.asyncio
@patch(AU + 'BlocksContextManager')
async def test_check_permission_exception(mock_bcm):
    mock_bcm.get_context.side_effect = Exception('boom')
    assert await auth._check_permission('res', ['r'], ['p'], 'tid', MagicMock()) is False


def test_authorize_missing_resource_raises():
    with pytest.raises(ValueError):
        auth.authorize()


@pytest.mark.asyncio
@patch(AU + 'check_standard_access', new_callable=AsyncMock)
@patch(AU + 'authenticate', new_callable=AsyncMock)
@patch(AU + 'BlocksContextManager')
@patch(AU + 'DbContext')
@patch(AU + 'CacheProvider')
@patch(AU + 'TenantService')
async def test_authorize_dependency_success(mock_ts, mock_cp, mock_db, mock_bcm, mock_auth, mock_csa):
    dep = auth.authorize('res')
    ctx = MagicMock(); mock_bcm.get_context.return_value = ctx
    mock_csa.return_value = True
    assert await dep.dependency(MagicMock()) is ctx


@pytest.mark.asyncio
@patch(AU + 'authenticate', new_callable=AsyncMock)
@patch(AU + 'BlocksContextManager')
@patch(AU + 'DbContext')
@patch(AU + 'CacheProvider')
@patch(AU + 'TenantService')
async def test_authorize_dependency_no_context(mock_ts, mock_cp, mock_db, mock_bcm, mock_auth):
    dep = auth.authorize('res')
    mock_bcm.get_context.return_value = None
    with pytest.raises(HTTPException):
        await dep.dependency(MagicMock())


@pytest.mark.asyncio
@patch(AU + 'authenticate', new_callable=AsyncMock)
@patch(AU + 'BlocksContextManager')
@patch(AU + 'DbContext')
@patch(AU + 'CacheProvider')
@patch(AU + 'TenantService')
async def test_authorize_dependency_bypass(mock_ts, mock_cp, mock_db, mock_bcm, mock_auth):
    dep = auth.authorize(bypass_authorization=True)
    ctx = MagicMock(); mock_bcm.get_context.return_value = ctx
    assert await dep.dependency(MagicMock()) is ctx


@pytest.mark.asyncio
@patch(AU + 'check_standard_access', new_callable=AsyncMock)
@patch(AU + 'authenticate', new_callable=AsyncMock)
@patch(AU + 'BlocksContextManager')
@patch(AU + 'DbContext')
@patch(AU + 'CacheProvider')
@patch(AU + 'TenantService')
async def test_authorize_dependency_no_access(mock_ts, mock_cp, mock_db, mock_bcm, mock_auth, mock_csa):
    dep = auth.authorize('res')
    ctx = MagicMock(); mock_bcm.get_context.return_value = ctx
    mock_csa.return_value = False
    with pytest.raises(HTTPException):
        await dep.dependency(MagicMock())


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_fetch_returns_none(mock_fetch):
    tenant = MagicMock()
    tenant.jwt_token_parameters.public_certificate_path = '/p'
    cache = MagicMock(); cache.get_bytes_value.return_value = None
    mock_fetch.return_value = None
    assert await auth.get_tenant_cert(cache, tenant, 'tid') is None


@pytest.mark.asyncio
@patch(AU + '_validate_via_jwks', new_callable=AsyncMock)
async def test_validate_with_fallback_jwks_fails_no_cert_path(mock_jwks):
    tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.jwks_url = 'http://j'
    tenant.third_party_jwt_token_parameters.public_certificate_path = None
    mock_jwks.return_value = None
    assert await auth.validate_with_fallback('tok', tenant, MagicMock()) is None


@pytest.mark.asyncio
@patch(AU + '_validate_via_public_cert', new_callable=AsyncMock)
async def test_validate_with_fallback_no_jwks_url_cert_success(mock_pc):
    tenant = MagicMock()
    tenant.third_party_jwt_token_parameters.jwks_url = None
    tenant.third_party_jwt_token_parameters.public_certificate_path = '/p'
    mock_pc.return_value = {'sub': 'u3'}
    req = MagicMock(); req.url = 'http://x'
    assert (await auth.validate_with_fallback('tok', tenant, req))['sub'] == 'u3'


@pytest.mark.asyncio
@patch(AU + 'fetch_cert_bytes', new_callable=AsyncMock)
async def test_get_tenant_cert_naive_issue_date(mock_fetch):
    tenant = MagicMock()
    tenant.jwt_token_parameters.issue_date = datetime.now()
    tenant.jwt_token_parameters.certificate_valid_for_number_of_days = 365
    cache = MagicMock(); cache.get_bytes_value.return_value = None
    cache.add_bytes_value_async = AsyncMock()
    mock_fetch.return_value = b'cb'
    assert await auth.get_tenant_cert(cache, tenant, 'tid') == b'cb'
