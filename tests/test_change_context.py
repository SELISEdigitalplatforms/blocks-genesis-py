import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._core import change_context

@pytest.mark.asyncio
@patch('blocks_genesis._core.change_context.BlocksContextManager')
@patch('blocks_genesis._core.change_context.Activity')
@patch('blocks_genesis._core.change_context.get_tenant_service')
async def test_change_context_noop(mock_get_tenant_service, mock_activity, mock_context_mgr):
    context = MagicMock()
    context.tenant_id = 'tid'
    mock_context_mgr.get_context.return_value = context
    # project_key is None
    await change_context.change_context(None)
    # project_key is empty
    await change_context.change_context("")
    # project_key is same as tenant_id
    await change_context.change_context('tid')
    # Should not change context
    mock_context_mgr.set_context.assert_not_called()

@pytest.mark.asyncio
@patch('blocks_genesis._core.change_context.BlocksContextManager')
@patch('blocks_genesis._core.change_context.Activity')
@patch('blocks_genesis._core.change_context.get_tenant_service')
async def test_change_context_root(mock_get_tenant_service, mock_activity, mock_context_mgr):
    context = MagicMock()
    context.tenant_id = 'tid'
    context.roles = ['r']
    context.user_id = 'u'
    context.is_authenticated = True
    context.request_uri = '/uri'
    context.organization_id = 'org'
    context.expire_on = None
    context.email = 'e@e.com'
    context.permissions = ['perm']
    context.user_name = 'uname'
    context.phone_number = '123'
    context.display_name = 'disp'
    context.oauth_token = 'token'
    mock_context_mgr.get_context.return_value = context
    tenant = MagicMock()
    tenant._id = True
    mock_get_tenant_service.return_value.get_tenant.return_value = tenant
    await change_context.change_context('newtid')
    mock_context_mgr.set_context.assert_called() 