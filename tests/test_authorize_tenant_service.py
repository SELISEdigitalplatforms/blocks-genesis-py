import logging

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from blocks_genesis._auth import auth
from blocks_genesis._tenant import tenant_service as tenant_service_module
from blocks_genesis._tenant.tenant import Tenant

AU = 'blocks_genesis._auth.auth.'
TS = 'blocks_genesis._tenant.tenant_service.'


def _tenant(tenant_id='t1', disabled=False):
    return Tenant(_id=tenant_id, TenantId=tenant_id, IsDisabled=disabled)


async def _call_authorize_twice():
    dep = auth.authorize(bypass_authorization=True)
    with patch(AU + 'authenticate', new_callable=AsyncMock) as mock_auth, \
         patch(AU + 'CacheProvider'), \
         patch(AU + 'DbContext'), \
         patch(AU + 'BlocksContextManager') as mock_bcm:
        mock_bcm.get_context.return_value = MagicMock()
        await dep.dependency(MagicMock())
        await dep.dependency(MagicMock())
    return mock_auth


# ---------------- authorize(): one tenant service per process ----------------

@pytest.mark.asyncio
async def test_authorize_reuses_the_process_tenant_service(monkeypatch, caplog):
    singleton = MagicMock()
    monkeypatch.setattr(tenant_service_module, '_tenant_service', singleton)

    with caplog.at_level(logging.WARNING, logger=auth.__name__), \
         patch(AU + 'TenantService') as per_request:
        mock_auth = await _call_authorize_twice()

    per_request.assert_not_called()
    assert mock_auth.await_count == 2
    for call in mock_auth.await_args_list:
        assert call.args[1] is singleton
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_authorize_without_singleton_creates_it_once(monkeypatch, caplog):
    monkeypatch.setattr(tenant_service_module, '_tenant_service', None)
    created = MagicMock()
    created.initialize = AsyncMock()

    with caplog.at_level(logging.WARNING, logger=auth.__name__), \
         patch(AU + 'TenantService') as per_request, \
         patch(TS + 'TenantService', return_value=created) as constructor:
        mock_auth = await _call_authorize_twice()

    per_request.assert_not_called()
    constructor.assert_called_once()
    created.initialize.assert_awaited()
    for call in mock_auth.await_args_list:
        assert call.args[1] is created
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


# ---------------- authenticate(): disabled tenants are refused ----------------

@pytest.mark.asyncio
async def test_authenticate_refuses_disabled_tenant_before_token_validation():
    request = MagicMock()
    ts = MagicMock()
    ts.get_tenant = AsyncMock(return_value=_tenant(disabled=True))

    with patch(AU + 'extract_token_from_request', new_callable=AsyncMock) as extract, \
         patch(AU + 'BlocksContextManager') as mock_bcm, \
         patch(AU + '_validate_authenticated_token', new_callable=AsyncMock) as validate:
        extract.return_value = ('tok', False, 'd.com')
        mock_bcm.get_context.return_value = MagicMock(tenant_id='t1')
        with pytest.raises(HTTPException) as exc:
            await auth.authenticate(request, ts, MagicMock())

    assert exc.value.status_code == 401
    assert exc.value.detail == 'Tenant not found'
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticate_accepts_enabled_tenant():
    request = MagicMock()
    ts = MagicMock()
    ts.get_tenant = AsyncMock(return_value=_tenant(disabled=False))

    with patch(AU + 'extract_token_from_request', new_callable=AsyncMock) as extract, \
         patch(AU + 'BlocksContextManager') as mock_bcm, \
         patch(AU + 'AuthClaimsContext'), \
         patch(AU + 'Activity'), \
         patch(AU + '_validate_authenticated_token', new_callable=AsyncMock) as validate:
        extract.return_value = ('tok', False, 'd.com')
        mock_bcm.get_context.return_value = MagicMock(tenant_id='t1')
        validate.return_value = ({'sub': 'u1'}, None)
        assert await auth.authenticate(request, ts, MagicMock()) == {'sub': 'u1'}

    validate.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_signer_tenant_is_refused_without_falling_back():
    tenant = _tenant('t1')
    ts = MagicMock()
    ts.get_tenant = AsyncMock(return_value=_tenant('home', disabled=True))

    with patch(AU + 'jwt') as mj, \
         patch(AU + 'validate_jwt_token', new_callable=AsyncMock) as validate:
        mj.decode.return_value = {'impersonated': True, 'original_tenant_id': 'home'}
        with pytest.raises(HTTPException) as exc:
            await auth._validate_authenticated_token(
                'tok', False, tenant, 't1', MagicMock(), MagicMock(), ts
            )

    assert exc.value.status_code == 401
    assert exc.value.detail == 'Tenant not found'
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_signer_tenant_is_used():
    tenant = _tenant('t1')
    signer = _tenant('home')
    ts = MagicMock()
    ts.get_tenant = AsyncMock(return_value=signer)

    with patch(AU + 'jwt') as mj:
        mj.decode.return_value = {'impersonated': True, 'original_tenant_id': 'home'}
        assert await auth._resolve_signing_tenant('tok', tenant, 't1', ts) is signer
