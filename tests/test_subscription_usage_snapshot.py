"""blocks_genesis._auth.auth.subscription_usage_snapshot.

Resolves a usage snapshot into SubscriptionUsageContext, standalone or composed after
authorize(). Unlike test_authorize_bypass, these tests actually invoke the inner
dependency(request).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContext
from blocks_genesis._subscription.context import SubscriptionUsageContext

AUTH = "blocks_genesis._auth.auth."


@pytest.fixture(autouse=True)
def _clear_usage_context():
    """These tests set the ambient snapshot; leaving it set would leak into other files."""
    SubscriptionUsageContext.clear()
    yield
    SubscriptionUsageContext.clear()


def _dep(**kwargs):
    """Return the inner dependency(request) coroutine function, unwrapped from Depends."""
    return auth.subscription_usage_snapshot(**kwargs).dependency


def _request():
    return MagicMock()


def test_factory_returns_a_depends_wrapping_a_callable():
    result = auth.subscription_usage_snapshot(bypass_authorization=True)
    assert result is not None
    assert callable(result.dependency)


# ---------------- bypass_authorization=False (compose after authorize(...)) ----------------


@pytest.mark.asyncio
async def test_no_prior_context_raises_401():
    with patch(AUTH + "BlocksContextManager") as mock_ctx_mgr:
        mock_ctx_mgr.get_context.return_value = None
        with pytest.raises(HTTPException) as exc:
            await _dep(bypass_authorization=False)(_request())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_reuses_existing_context_without_reauthenticating():
    ctx = BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "authenticate", new_callable=AsyncMock) as mock_authenticate,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(return_value=[{"meterKey": "messages", "allowed": True}])

        result = await _dep(bypass_authorization=False)(_request())

    mock_authenticate.assert_not_awaited()
    assert result is ctx
    assert SubscriptionUsageContext.current() == [{"meterKey": "messages", "allowed": True}]
    mock_service.get_usage_current.assert_awaited_once_with(tenant_id="t1", organization_id="org-1")


# ---------------- bypass_authorization=True (standalone) ----------------


@pytest.mark.asyncio
async def test_standalone_mode_delegates_to_authorize_bypass():
    # Must delegate to authorize(bypass_authorization=True) itself, not
    # reimplement its bypass steps -- otherwise the two silently drift apart
    # if authorize()'s own internals ever change.
    ctx = BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=True)
    fake_dependency = AsyncMock(return_value=ctx)
    fake_depends = MagicMock(dependency=fake_dependency)
    with (
        patch(AUTH + "authorize", return_value=fake_depends) as mock_authorize,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_service.get_usage_current = AsyncMock(return_value=[])

        request = _request()
        result = await _dep(bypass_authorization=True)(request)

    mock_authorize.assert_called_once_with(bypass_authorization=True)
    fake_dependency.assert_awaited_once_with(request)
    assert result is ctx


@pytest.mark.asyncio
async def test_standalone_mode_propagates_401_raised_by_authorize():
    fake_dependency = AsyncMock(side_effect=HTTPException(status_code=401, detail="Missing context"))
    fake_depends = MagicMock(dependency=fake_dependency)
    with patch(AUTH + "authorize", return_value=fake_depends):
        with pytest.raises(HTTPException) as exc:
            await _dep(bypass_authorization=True)(_request())
    assert exc.value.status_code == 401


# ---------------- organization resolution ----------------


@pytest.mark.asyncio
async def test_missing_organization_id_leaves_snapshot_none_without_querying():
    ctx = BlocksContext(tenant_id="t1", organization_id="", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx

        result = await _dep(bypass_authorization=False)(_request())

    mock_service.get_usage_current.assert_not_called()
    assert result is ctx
    assert SubscriptionUsageContext.current() is None


@pytest.mark.asyncio
async def test_uses_context_tenant_id_and_organization_id():
    ctx = BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(return_value=[])

        await _dep(bypass_authorization=False)(_request())

    mock_service.get_usage_current.assert_awaited_once_with(tenant_id="t1", organization_id="org-1")


# ---------------- failure modes: fail open, never raise ----------------


@pytest.mark.asyncio
async def test_db_error_leaves_snapshot_none_and_does_not_raise():
    ctx = BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(side_effect=ConnectionError("mongo down"))

        result = await _dep(bypass_authorization=False)(_request())

    assert SubscriptionUsageContext.current() is None
