"""blocks_genesis._subscription.usage_service.SubscriptionUsageService.

Direct-Mongo read path (get_usage_current), backed by an in-memory fake collection standing
in for pymongo -- these test the orchestration logic, not real Mongo.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from blocks_genesis._subscription.usage_service import SubscriptionUsageService
from blocks_genesis._subscription.models import UsageResult

REPO = "blocks_genesis._subscription.repository."


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    def find_one(self, filt):
        for doc in self.docs.values():
            if _matches(doc, filt):
                return dict(doc)
        return None


def _matches(doc, filt):
    for key, value in filt.items():
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class _FakeProvider:
    def __init__(self):
        self.collections = {
            "Subscriptions": _FakeCollection(),
            "SubscriptionUsageCounters": _FakeCollection(),
        }

    async def get_collection(self, name, tenant_id=None):
        return self.collections[name]


@pytest.fixture
def provider():
    fake = _FakeProvider()
    with patch(REPO + "DbContext") as mock_db_context:
        mock_db_context.get_provider.return_value = fake
        yield fake


def _meter(key="messages", included=100, reset_policy=0, carry_cap=None, overage=True):
    return {
        "MeterKey": key,
        "ResetPolicy": reset_policy,
        "IncludedQuantity": included,
        "CarryForwardCap": carry_cap,
        "OverageAllowed": overage,
    }


def _subscription(sub_id="sub-1", tenant_id="t1", organization_id="org-1", status=3, meters=None, trial=None):
    return {
        "_id": sub_id,
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "Status": status,
        "CreatedAtUtc": datetime(2026, 1, 1),
        "UsageSchedule": {
            "Interval": 2,  # Month
            "IntervalCount": 1,
            "AnchorInstantUtc": datetime(2026, 1, 1),
            "TimeZoneId": "UTC",
            "AnchorDayOfMonth": 1,
            "AnchorMinutesFromMidnight": 0,
        },
        "Trial": trial,
        "Plan": {"Meters": meters if meters is not None else [_meter()]},
    }


def _seed_subscription(provider_, doc):
    provider_.collections["Subscriptions"].docs[doc["_id"]] = doc


@pytest.mark.asyncio
async def test_requires_tenant_and_org():
    assert await SubscriptionUsageService.get_usage_current(tenant_id="", organization_id="org-1") is None
    assert await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="") is None


@pytest.mark.asyncio
async def test_no_live_subscription_returns_empty_list(provider):
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="org-1")
    assert result == []


@pytest.mark.asyncio
async def test_subscription_with_no_meters_returns_empty_list(provider):
    _seed_subscription(provider, _subscription(meters=[]))
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="org-1")
    assert result == []


@pytest.mark.asyncio
async def test_no_counter_yet_reports_full_allowance(provider):
    _seed_subscription(provider, _subscription())
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="org-1")
    assert len(result) == 1
    assert isinstance(result[0], UsageResult)
    assert result[0].meter_key == "messages"
    assert result[0].used == 0
    assert result[0].remaining == 100
    assert result[0].allowed is True


@pytest.mark.asyncio
async def test_reflects_existing_counter_balance(provider):
    # Never-reset so the period key is always LIFETIME, independent of the real "now"
    # get_usage_current() resolves internally.
    _seed_subscription(provider, _subscription(meters=[_meter(reset_policy=1, included=100)]))
    provider.collections["SubscriptionUsageCounters"].docs["sub-1:messages:LIFETIME"] = {
        "_id": "sub-1:messages:LIFETIME",
        "Balance": 30,
        "LimitSnapshot": 100,
    }
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="org-1")
    assert result[0].used == 30
    assert result[0].remaining == 70


@pytest.mark.asyncio
async def test_meter_with_unresolvable_period_is_skipped_not_crashed(provider):
    sub = _subscription()
    sub["UsageSchedule"]["TimeZoneId"] = "Not/AZone"
    _seed_subscription(provider, sub)
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="org-1")
    assert result == []


@pytest.mark.asyncio
async def test_db_error_returns_none(provider):
    with patch(REPO + "get_live_subscription", side_effect=ConnectionError("down")):
        result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="org-1")
    assert result is None
