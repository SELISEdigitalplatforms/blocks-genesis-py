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


# ---------------- fallback ids for a caller that carries no context ----------------


@pytest.mark.asyncio
async def test_fallback_ids_are_used_when_there_is_no_context():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None
        mock_service.get_usage_current = AsyncMock(return_value=[])

        result = await _dep(tenant_id="t9", organization_id="org-9")(_request())

    assert result is None
    mock_service.get_usage_current.assert_awaited_once_with(tenant_id="t9", organization_id="org-9")
    assert SubscriptionUsageContext.current() == []


@pytest.mark.asyncio
async def test_half_a_fallback_identity_still_raises_401():
    with patch(AUTH + "BlocksContextManager") as mock_ctx_mgr:
        mock_ctx_mgr.get_context.return_value = None
        with pytest.raises(HTTPException) as exc:
            await _dep(tenant_id="t9")(_request())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_the_context_wins_over_the_fallback_ids():
    ctx = BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(return_value=[])

        await _dep(tenant_id="t9", organization_id="org-9")(_request())

    mock_service.get_usage_current.assert_awaited_once_with(tenant_id="t1", organization_id="org-1")


@pytest.mark.asyncio
async def test_a_fallback_id_fills_in_what_the_context_lacks():
    ctx = BlocksContext(tenant_id="t1", organization_id="", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(return_value=[])

        await _dep(organization_id="org-9")(_request())

    mock_service.get_usage_current.assert_awaited_once_with(tenant_id="t1", organization_id="org-9")


# ---------------- resolve_subscription_usage: the same thing without FastAPI ----------------


@pytest.mark.asyncio
async def test_resolver_works_with_ids_alone_and_no_request():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None
        mock_service.get_usage_current = AsyncMock(return_value=[])

        snapshot = await auth.resolve_subscription_usage(tenant_id="t9", organization_id="org-9")

    assert snapshot == []
    assert SubscriptionUsageContext.current() == []


@pytest.mark.asyncio
async def test_resolver_without_any_identity_leaves_it_none_without_querying():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None

        assert await auth.resolve_subscription_usage() is None

    mock_service.get_usage_current.assert_not_called()
    assert SubscriptionUsageContext.current() is None


@pytest.mark.asyncio
async def test_resolver_swallows_a_read_failure():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None
        mock_service.get_usage_current = AsyncMock(side_effect=ConnectionError("mongo down"))

        assert await auth.resolve_subscription_usage(tenant_id="t9", organization_id="org-9") is None

    assert SubscriptionUsageContext.current() is None


# ---------------- resolve_subscription_usage_snapshot: both sides in one read ----------------


def _rows(used):
    from blocks_genesis import UsageResult

    return [UsageResult(allowed=True, meter_key="ai-credits", used=used, remaining=90,
                        overage=0, replayed=False)]


@pytest.mark.asyncio
async def test_the_snapshot_resolver_reads_both_sides_and_publishes_the_organizations():
    from blocks_genesis import UsageSnapshot

    snapshot = UsageSnapshot(organization=_rows(10), member={"u1": _rows(4)})
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None
        mock_service.get_usage_snapshot = AsyncMock(return_value=snapshot)

        result = await auth.resolve_subscription_usage_snapshot(
            tenant_id="t9", organization_id="org-9", user_id="u1"
        )

    assert result.member["u1"][0].used == 4
    # The context keeps what everything already reads from it: the organization's rows.
    assert SubscriptionUsageContext.current()[0].used == 10
    mock_service.get_usage_snapshot.assert_awaited_once_with(
        tenant_id="t9", organization_id="org-9", user_id="u1", entitlements=False
    )


@pytest.mark.asyncio
async def test_the_snapshot_resolver_takes_the_tenant_and_organization_from_the_context():
    from blocks_genesis import UsageSnapshot

    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = BlocksContext(
            tenant_id="t-ctx", organization_id="org-ctx"
        )
        mock_service.get_usage_snapshot = AsyncMock(return_value=UsageSnapshot(organization=[]))

        await auth.resolve_subscription_usage_snapshot(
            tenant_id="t9", organization_id="org-9", user_id="u1"
        )

    mock_service.get_usage_snapshot.assert_awaited_once_with(
        tenant_id="t-ctx", organization_id="org-ctx", user_id="u1", entitlements=False
    )


@pytest.mark.asyncio
async def test_the_snapshot_resolver_without_ids_reads_nothing():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None

        assert await auth.resolve_subscription_usage_snapshot(user_id="u1") is None

    mock_service.get_usage_snapshot.assert_not_called()
    assert SubscriptionUsageContext.current() is None


@pytest.mark.asyncio
async def test_the_snapshot_resolver_swallows_a_read_failure():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None
        mock_service.get_usage_snapshot = AsyncMock(side_effect=ConnectionError("mongo down"))

        assert await auth.resolve_subscription_usage_snapshot(
            tenant_id="t9", organization_id="org-9", user_id="u1"
        ) is None

    assert SubscriptionUsageContext.current() is None


def _snapshot_service(mock_service):
    from blocks_genesis import UsageSnapshot

    mock_service.get_usage_snapshot = AsyncMock(return_value=UsageSnapshot(organization=[]))
    return mock_service


@pytest.mark.asyncio
async def test_the_signed_in_user_is_the_member_when_the_caller_does_not_say():
    """A valid token is enough: the member comes from the context without being asked."""
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = BlocksContext(
            tenant_id="t-ctx", organization_id="org-ctx", user_id="u-ctx", is_authenticated=True
        )
        _snapshot_service(mock_service)

        await auth.resolve_subscription_usage_snapshot()

    mock_service.get_usage_snapshot.assert_awaited_once_with(
        tenant_id="t-ctx", organization_id="org-ctx", user_id="u-ctx", entitlements=False
    )


@pytest.mark.asyncio
async def test_an_empty_user_id_means_no_member_even_with_one_signed_in():
    """A widget visitor's token carries a user id too; the caller opts them out with ""."""
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = BlocksContext(
            tenant_id="t-ctx", organization_id="org-ctx", user_id="visitor", is_authenticated=True
        )
        _snapshot_service(mock_service)

        await auth.resolve_subscription_usage_snapshot(user_id="")

    mock_service.get_usage_snapshot.assert_awaited_once_with(
        tenant_id="t-ctx", organization_id="org-ctx", user_id=None, entitlements=False
    )


@pytest.mark.asyncio
async def test_a_named_member_wins_over_the_signed_in_one():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = BlocksContext(
            tenant_id="t-ctx", organization_id="org-ctx", user_id="u-ctx", is_authenticated=True
        )
        _snapshot_service(mock_service)

        await auth.resolve_subscription_usage_snapshot(user_id="u-named")

    assert mock_service.get_usage_snapshot.await_args.kwargs["user_id"] == "u-named"


@pytest.mark.asyncio
async def test_an_unauthenticated_context_names_no_member():
    """No valid token, no signed-in user to read a balance for."""
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = BlocksContext(
            tenant_id="t-ctx", organization_id="org-ctx", user_id="u-ctx", is_authenticated=False
        )
        _snapshot_service(mock_service)

        await auth.resolve_subscription_usage_snapshot()

    assert mock_service.get_usage_snapshot.await_args.kwargs["user_id"] is None
