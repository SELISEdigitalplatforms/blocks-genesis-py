"""subscription_usage_snapshot() needs a signed-in caller, and the usage row it reads is
the current subscription's.

Two things the dependency and the repository must hold to:

- The tenant middleware sets an anonymous context on every request. That context is truthy,
  but it is not a sign-in, so without fallback ids it is a 401 and no usage is read.
- During a plan change two live subscriptions overlap. The repository sorts the newest
  subscription's rows first, so `for_meter` answers from the current plan and not from
  whatever order Mongo returns.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContext
from blocks_genesis._subscription import repository
from blocks_genesis._subscription.context import SubscriptionUsageContext
from blocks_genesis._subscription.usage_service import SubscriptionUsageService

AUTH = "blocks_genesis._auth.auth."
REPO = "blocks_genesis._subscription.repository."

NEWEST_FIRST = [("PeriodStartUtc", -1), ("_id", -1)]


@pytest.fixture(autouse=True)
def _clear_usage_context():
    SubscriptionUsageContext.clear()
    yield
    SubscriptionUsageContext.clear()


def _dep(**kwargs):
    return auth.subscription_usage_snapshot(**kwargs).dependency


def _anonymous():
    """What the tenant middleware sets before any sign-in: ids from the key, no user."""
    return BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=False)


# ---------------- the dependency: a signed-in caller or explicit ids ----------------


@pytest.mark.asyncio
async def test_an_anonymous_context_without_fallback_ids_is_a_401_and_reads_nothing():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = _anonymous()
        mock_service.get_usage_current = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc:
            await _dep(bypass_authorization=False)(MagicMock())

    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing context"
    mock_service.get_usage_current.assert_not_called()
    assert SubscriptionUsageContext.current() is None


@pytest.mark.asyncio
async def test_an_authenticated_context_reads_usage_as_before():
    ctx = BlocksContext(tenant_id="t1", organization_id="org-1", is_authenticated=True)
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(return_value=[])

        result = await _dep(bypass_authorization=False)(MagicMock())

    assert result is ctx
    mock_service.get_usage_current.assert_awaited_once_with(
        tenant_id="t1", organization_id="org-1"
    )
    assert SubscriptionUsageContext.current() == []


@pytest.mark.asyncio
async def test_the_explicit_fallback_ids_still_work_without_a_context():
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = None
        mock_service.get_usage_current = AsyncMock(return_value=[])

        result = await _dep(tenant_id="t9", organization_id="org-9")(MagicMock())

    assert result is None
    mock_service.get_usage_current.assert_awaited_once_with(
        tenant_id="t9", organization_id="org-9"
    )


@pytest.mark.asyncio
async def test_the_explicit_fallback_ids_still_work_beside_an_anonymous_context():
    # The caller named the ids itself, so it behaves as it always has: the context's ids win.
    ctx = _anonymous()
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(AUTH + "SubscriptionUsageService") as mock_service,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_service.get_usage_current = AsyncMock(return_value=[])

        result = await _dep(tenant_id="t9", organization_id="org-9")(MagicMock())

    assert result is ctx
    mock_service.get_usage_current.assert_awaited_once_with(
        tenant_id="t1", organization_id="org-1"
    )


# ---------------- the repository: newest subscription first ----------------


class _SortingCollection:
    """Applies the filter's equality keys and the requested sort, the way Mongo would."""

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def find(self, filt, sort=None):
        self.calls.append((filt, sort))
        rows = [
            d for d in self.docs
            if d["TenantId"] == filt["TenantId"] and d["OrganizationId"] == filt["OrganizationId"]
        ]
        for field, direction in reversed(sort or []):
            rows.sort(key=lambda d: d[field], reverse=direction < 0)
        return rows


def _row(subscription_id, period_start, used, *, user_id=""):
    return {
        "_id": f"{subscription_id}:credits:{period_start:%Y%m%d}" + (f":{user_id}" if user_id else ""),
        "TenantId": "t1",
        "OrganizationId": "org-1",
        "UserId": user_id,
        "SubscriptionId": subscription_id,
        "SubscriptionStatus": 3,
        "MeterKey": "credits",
        "PeriodStartUtc": period_start,
        "PeriodEndUtc": period_start + timedelta(days=30),
        "Included": 100,
        "Used": used,
        "Remaining": 100 - used,
        "Overage": 0,
        "OverageAllowed": False,
    }


def _overlap():
    """An old plan still live beside the new one, stored old-first in natural order."""
    now = datetime.now(timezone.utc)
    old = _row("sub-old", now - timedelta(days=20), used=90)
    new = _row("sub-new", now - timedelta(days=1), used=5)
    return old, new


def _provider_for(collection):
    provider = MagicMock()
    provider.get_collection = AsyncMock(return_value=collection)
    return provider


@pytest.mark.asyncio
async def test_the_organization_usage_query_sorts_newest_subscription_first():
    collection = _SortingCollection([])
    with patch(REPO + "DbContext") as mock_db:
        mock_db.get_provider.return_value = _provider_for(collection)
        await repository.get_current_usage_docs("t1", "org-1")

    (_, sort), = collection.calls
    assert sort == NEWEST_FIRST


@pytest.mark.asyncio
async def test_the_scoped_usage_query_sorts_newest_subscription_first():
    collection = _SortingCollection([])
    with patch(REPO + "DbContext") as mock_db:
        mock_db.get_provider.return_value = _provider_for(collection)
        await repository.get_scoped_usage_docs("t1", "org-1", ["u1"])

    (_, sort), = collection.calls
    assert sort == NEWEST_FIRST


@pytest.mark.asyncio
async def test_for_meter_answers_from_the_newer_of_two_overlapping_subscriptions():
    old, new = _overlap()
    collection = _SortingCollection([old, new])
    with patch(REPO + "DbContext") as mock_db:
        mock_db.get_provider.return_value = _provider_for(collection)
        rows = await SubscriptionUsageService.get_usage_current(
            tenant_id="t1", organization_id="org-1"
        )

    SubscriptionUsageContext.set(rows)
    row = SubscriptionUsageContext.for_meter("credits")
    assert row is not None
    assert row.used == 5
    assert collection.calls[0][1] == NEWEST_FIRST


@pytest.mark.asyncio
async def test_the_snapshot_path_also_answers_from_the_newer_subscription():
    old, new = _overlap()
    collection = _SortingCollection([old, new])
    ctx = BlocksContext(
        tenant_id="t1", organization_id="org-1", user_id="u1", is_authenticated=True
    )
    with (
        patch(AUTH + "BlocksContextManager") as mock_ctx_mgr,
        patch(REPO + "DbContext") as mock_db,
    ):
        mock_ctx_mgr.get_context.return_value = ctx
        mock_db.get_provider.return_value = _provider_for(collection)
        await auth.resolve_subscription_usage_snapshot()

    row = SubscriptionUsageContext.for_meter("credits")
    assert row is not None
    assert row.used == 5
    assert collection.calls[0][1] == NEWEST_FIRST


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known limit: the sort is latest period first, not newest subscription first. An old "
        "subscription that renews during a plan-change overlap opens a later period and wins. "
        "Needs a subscription start field on the usage read model (blocks-utilities)."
    ),
)
@pytest.mark.asyncio
async def test_an_old_subscription_that_renews_during_the_overlap_still_loses_to_the_new_one():
    now = datetime.now(timezone.utc)
    new = _row("sub-new", now - timedelta(days=3), used=5)
    # The old plan renewed after the new one started, so its current period is the later one.
    old_renewed = _row("sub-old", now - timedelta(days=1), used=90)
    collection = _SortingCollection([new, old_renewed])
    with patch(REPO + "DbContext") as mock_db:
        mock_db.get_provider.return_value = _provider_for(collection)
        rows = await SubscriptionUsageService.get_usage_current(
            tenant_id="t1", organization_id="org-1"
        )

    SubscriptionUsageContext.set(rows)
    row = SubscriptionUsageContext.for_meter("credits")
    assert row is not None
    assert row.used == 5
