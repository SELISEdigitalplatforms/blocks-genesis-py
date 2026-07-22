from unittest.mock import MagicMock, patch
from blocks_genesis._auth.blocks_context import BlocksContextManager, BlocksContext

BC = 'blocks_genesis._auth.blocks_context.'


def test_normalize_domain_empty_and_blank():
    assert BlocksContextManager.normalize_domain('') == ''
    assert BlocksContextManager.normalize_domain('   ') == ''


def test_normalize_domain_urlparse_exception_fallback():
    result = BlocksContextManager.normalize_domain('http://[::1')
    assert isinstance(result, str)


def test_normalize_domain_hostname():
    assert BlocksContextManager.normalize_domain('https://Example.com/path') == 'example.com'


def test_resolve_application_domain_origin():
    req = MagicMock()
    req.headers.get.side_effect = lambda k: 'http://a.com' if k == 'Origin' else None
    assert BlocksContextManager.resolve_application_domain(req) == 'a.com'


def test_resolve_application_domain_referer():
    req = MagicMock()
    req.headers.get.side_effect = lambda k: 'http://b.com' if k == 'Referer' else None
    assert BlocksContextManager.resolve_application_domain(req) == 'b.com'


def test_resolve_application_domain_none():
    req = MagicMock()
    req.headers.get.return_value = None
    assert BlocksContextManager.resolve_application_domain(req) is None


def test_create_from_jwt_claims_string_roles_int_exp():
    claims = {
        BlocksContext.ROLES_CLAIM: 'admin',
        BlocksContext.EXPIRE_ON_CLAIM: 1700000000,
        BlocksContext.TENANT_ID_CLAIM: 'tid',
    }
    ctx = BlocksContextManager.create_from_jwt_claims(claims)
    assert ctx.roles == ['admin']


def test_create_from_jwt_claims_str_exp_and_original_from_claim():
    claims = {
        BlocksContext.EXPIRE_ON_CLAIM: '2030-01-01T00:00:00Z',
        BlocksContext.ORIGINAL_TENANT_ID_CLAIM: 'ot',
        BlocksContext.TENANT_ID_CLAIM: 'tid',
    }
    ctx = BlocksContextManager.create_from_jwt_claims(claims)
    assert ctx is not None


def test_create_from_jwt_claims_bad_exp_and_original_from_tenant():
    claims = {
        BlocksContext.EXPIRE_ON_CLAIM: 'notadate',
        BlocksContext.TENANT_ID_CLAIM: 'tid',
    }
    ctx = BlocksContextManager.create_from_jwt_claims(claims)
    assert ctx is not None


def test_create_from_jwt_claims_no_exp_and_explicit_original():
    claims = {BlocksContext.TENANT_ID_CLAIM: 'tid'}
    ctx = BlocksContextManager.create_from_jwt_claims(claims, original_tenant_id='explicit')
    assert ctx is not None


def test_get_context_exception_returns_none():
    BlocksContextManager.set_test_mode(False)
    with patch(BC + '_context_var') as mock_cv:
        mock_cv.get.side_effect = Exception('boom')
        assert BlocksContextManager.get_context() is None


def test_normalize_domain_no_hostname_fallback():
    assert isinstance(BlocksContextManager.normalize_domain('/just/path'), str)


def test_resolve_application_domain_origin_blank_then_referer():
    req = MagicMock()
    req.headers.get.side_effect = lambda k: '   ' if k == 'Origin' else ('http://c.com' if k == 'Referer' else None)
    assert BlocksContextManager.resolve_application_domain(req) == 'c.com'


def test_resolve_application_domain_referer_blank_returns_none():
    req = MagicMock()
    req.headers.get.side_effect = lambda k: '   ' if k == 'Referer' else None
    assert BlocksContextManager.resolve_application_domain(req) is None


def test_create_from_jwt_claims_exp_non_str_non_num():
    claims = {BlocksContext.EXPIRE_ON_CLAIM: [1], BlocksContext.TENANT_ID_CLAIM: 'tid'}
    assert BlocksContextManager.create_from_jwt_claims(claims) is not None
