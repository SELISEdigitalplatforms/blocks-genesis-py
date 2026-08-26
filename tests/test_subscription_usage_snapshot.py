"""blocks_genesis._auth.auth.subscription_usage_snapshot.

A FastAPI dependency factory mirroring authorize()'s dependencies=[...] pattern --
resolves a current-usage snapshot onto BlocksContext.usage_snapshot at request entry,
either standalone (bypass_authorization=True, authenticates itself) or composed after
authorize(...) in the same dependencies=[...] list (bypass_authorization=False, the
default, reuses the context authorize() already populated).

Reads context.oauth_token directly -- authenticate() (via validate_jwt_token()/
validate_with_fallback()) already injects the real validated bearer token into the
decoded JWT payload under the "oauth" claim before BlocksContext is built from it, so
this dependency doesn't need a second token-resolution pass.

Unlike test_auth.py's test_authorize_bypass (which only checks the factory returns a
Depends), these tests actually invoke the inner dependency(request) coroutine -- see
docs/subscription-usage-gate-agents-card.md / subscription-usage-gate-CHANGES.md in
blocks-agents for the full design and cross-repo context.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContext

AUTH = "blocks_genesis._auth.auth."


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
    ctx = BlocksContext(tenant_id="t1", oauth_token="real-token", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "authenticate", new_callable=AsyncMock) as mock_authenticate,
        patch(AUTH + "SubscriptionClient") as mock_client_cls,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        client = AsyncMock()
        client.get_usage_current.return_value = [{"meterKey": "messages", "allowed": True}]
        mock_client_cls.get_instance.return_value = client

        result = await _dep(bypass_authorization=False)(_request())

    mock_authenticate.assert_not_awaited()
    assert result is ctx
    assert result.usage_snapshot == [{"meterKey": "messages", "allowed": True}]
    client.get_usage_current.assert_awaited_once_with(oauth_token="real-token", tenant_id="t1")


# ---------------- bypass_authorization=True (standalone) ----------------


@pytest.mark.asyncio
async def test_standalone_mode_delegates_to_authorize_bypass():
    # Must delegate to authorize(bypass_authorization=True) itself, not
    # reimplement its bypass steps -- otherwise the two silently drift apart
    # if authorize()'s own internals ever change.
    ctx = BlocksContext(tenant_id="t1", oauth_token="real-token", is_authenticated=True)
    fake_dependency = AsyncMock(return_value=ctx)
    fake_depends = MagicMock(dependency=fake_dependency)
    with (
        patch(AUTH + "authorize", return_value=fake_depends) as mock_authorize,
        patch(AUTH + "SubscriptionClient") as mock_client_cls,
    ):
        client = AsyncMock()
        client.get_usage_current.return_value = []
        mock_client_cls.get_instance.return_value = client

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


# ---------------- token resolution ----------------


@pytest.mark.asyncio
async def test_missing_oauth_token_leaves_snapshot_none_without_calling_utilities():
    ctx = BlocksContext(tenant_id="t1", oauth_token="", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionClient") as mock_client_cls,
    ):
        mock_ctx_mgr.get_context.return_value = ctx

        result = await _dep(bypass_authorization=False)(_request())

    mock_client_cls.get_instance.assert_not_called()
    assert result is ctx
    assert result.usage_snapshot is None


@pytest.mark.asyncio
async def test_uses_context_oauth_token_and_tenant_id():
    ctx = BlocksContext(tenant_id="t1", oauth_token="real-token", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionClient") as mock_client_cls,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        client = AsyncMock()
        client.get_usage_current.return_value = []
        mock_client_cls.get_instance.return_value = client

        await _dep(bypass_authorization=False)(_request())

    client.get_usage_current.assert_awaited_once_with(oauth_token="real-token", tenant_id="t1")


# ---------------- failure modes: fail open, never raise ----------------


@pytest.mark.asyncio
async def test_utilities_error_leaves_snapshot_none_and_does_not_raise():
    ctx = BlocksContext(tenant_id="t1", oauth_token="tok", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionClient") as mock_client_cls,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        client = AsyncMock()
        client.get_usage_current.side_effect = ConnectionError("network down")
        mock_client_cls.get_instance.return_value = client

        result = await _dep(bypass_authorization=False)(_request())

    assert result.usage_snapshot is None


@pytest.mark.asyncio
async def test_subscription_client_not_initialized_leaves_snapshot_none_and_does_not_raise():
    ctx = BlocksContext(tenant_id="t1", oauth_token="tok", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionClient") as mock_client_cls,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_client_cls.get_instance.side_effect = RuntimeError("SubscriptionClient not initialized.")

        result = await _dep(bypass_authorization=False)(_request())

    assert result.usage_snapshot is None
