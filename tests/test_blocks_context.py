import pytest
from datetime import datetime
from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager

@pytest.fixture(autouse=True)
def clear_context():
    BlocksContextManager.clear_context()
    yield
    BlocksContextManager.clear_context()

def test_blocks_context_defaults():
    ctx = BlocksContext()
    assert ctx.tenant_id == ""
    assert ctx.roles == []
    assert ctx.is_authenticated is False

def test_blocks_contextmanager_create_from_jwt_claims():
    claims = {
        'tenant_id': 'tid',
        'roles': ['admin'],
        'user_id': 'uid',
        'request_uri': '/uri',
        'org_id': 'oid',
        'exp': int(datetime.now().timestamp()),
        'email': 'e@e.com',
        'permissions': ['perm'],
        'user_name': 'uname',
        'phone': '123',
        'name': 'disp',
        'oauth': 'token',
    }
    ctx = BlocksContextManager.create_from_jwt_claims(claims)
    assert ctx.tenant_id == 'tid'
    assert ctx.roles == ['admin']
    assert ctx.user_id == 'uid'
    assert ctx.organization_id == 'oid'
    assert ctx.email == 'e@e.com'
    assert ctx.permissions == ['perm']
    assert ctx.user_name == 'uname'
    assert ctx.phone_number == '123'
    assert ctx.display_name == 'disp'
    assert ctx.oauth_token == 'token'
    assert ctx.is_authenticated is True

def test_blocks_contextmanager_create():
    ctx = BlocksContextManager.create(tenant_id='tid', roles=['r'], user_id='uid', is_authenticated=True)
    assert ctx.tenant_id == 'tid'
    assert ctx.roles == ['r']
    assert ctx.user_id == 'uid'
    assert ctx.is_authenticated is True

def test_blocks_contextmanager_set_and_get_context():
    ctx = BlocksContextManager.create(tenant_id='tid')
    BlocksContextManager.set_context(ctx)
    assert BlocksContextManager.get_context().tenant_id == 'tid'

def test_blocks_contextmanager_clear_context():
    ctx = BlocksContextManager.create(tenant_id='tid')
    BlocksContextManager.set_context(ctx)
    BlocksContextManager.clear_context()
    assert BlocksContextManager.get_context() is None

def test_blocks_contextmanager_test_mode():
    BlocksContextManager.set_test_mode(True)
    ctx = BlocksContextManager.create(tenant_id='tid')
    assert BlocksContextManager.get_test_mode() is True
    assert BlocksContextManager.get_context(test_value=ctx).tenant_id == 'tid'
    BlocksContextManager.set_test_mode(False)
    assert BlocksContextManager.get_test_mode() is False 