"""blocks_genesis._subscription.usage_service.SubscriptionUsageService.

Reads the SubscriptionUsageCurrent model straight from Mongo. Backed by an in-memory fake
collection standing in for pymongo -- these test the mapping and failure paths, not Mongo.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from blocks_genesis._subscription.usage_service import SubscriptionUsageService
from blocks_genesis._subscription.models import UsageResult

REPO = "blocks_genesis._subscription.repository."


class _FakeCollection:
    def __init__(self):
        self.docs = []
        self.last_filter = None

    def find(self, filt):
        self.last_filter = filt
        return [d for d in self.docs if _matches(d, filt)]


def _matches(doc, filt):
    for key, value in filt.items():
        actual = doc.get(key)
        if isinstance(value, dict):
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$lte" in value and not (actual is not None and actual <= value["$lte"]):
                return False
            if "$gt" in value and not (actual is not None and actual > value["$gt"]):
                return False
        elif actual != value:
            return False
    return True


class _FakeProvider:
    def __init__(self):
        self.collection = _FakeCollection()

    async def get_collection(self, name, tenant_id=None):
        return self.collection


@pytest.fixture
def provider():
    fake = _FakeProvider()
    with patch(REPO + "DbContext") as mock_db_context:
        mock_db_context.get_provider.return_value = fake
        yield fake


def _usage_doc(
    meter_key="tkn",
    tenant_id="t1",
    organization_id="default",
    status=2,
    included=500,
    used=800,
    remaining=0,
    overage=300,
    overage_allowed=True,
):
    now = datetime.utcnow()
    return {
        "_id": f"sub-1:{meter_key}:M20260902T024500Z",
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "SubscriptionId": "sub-1",
        "SubscriptionStatus": status,
        "MeterKey": meter_key,
        "UnitLabel": "token",
        "PeriodKey": "M20260902T024500Z",
        "PeriodStartUtc": now - timedelta(days=1),
        "PeriodEndUtc": now + timedelta(days=29),
        "Included": included,
        "Used": used,
        "Remaining": remaining,
        "Overage": overage,
        "OverageAllowed": overage_allowed,
    }


@pytest.mark.asyncio
async def test_requires_tenant_and_org():
    assert await SubscriptionUsageService.get_usage_current(tenant_id="", organization_id="default") is None
    assert await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="") is None


@pytest.mark.asyncio
async def test_no_rows_returns_empty_list(provider):
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result == []


@pytest.mark.asyncio
async def test_maps_a_real_row(provider):
    provider.collection.docs = [_usage_doc()]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert len(result) == 1
    row = result[0]
    assert isinstance(row, UsageResult)
    assert row.meter_key == "tkn"
    assert row.used == 800
    assert row.remaining == 0
    assert row.overage == 300
    assert row.allowed is True  # over the included 500, but OverageAllowed


@pytest.mark.asyncio
async def test_over_allowance_without_overage_is_not_allowed(provider):
    provider.collection.docs = [_usage_doc(used=800, included=500, overage_allowed=False)]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is False


@pytest.mark.asyncio
async def test_within_allowance_is_allowed(provider):
    provider.collection.docs = [_usage_doc(used=100, included=500, remaining=400, overage=0, overage_allowed=False)]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is True
    assert result[0].remaining == 400


@pytest.mark.asyncio
async def test_filters_by_tenant_org_active_status_and_current_period(provider):
    provider.collection.docs = [_usage_doc()]
    await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    filt = provider.collection.last_filter
    assert filt["TenantId"] == "t1"
    assert filt["OrganizationId"] == "default"
    assert filt["SubscriptionStatus"] == 2
    assert "$lte" in filt["PeriodStartUtc"]
    assert "$gt" in filt["PeriodEndUtc"]


@pytest.mark.asyncio
async def test_cancelled_subscription_row_is_excluded(provider):
    provider.collection.docs = [_usage_doc(status=4)]  # Canceled
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result == []


@pytest.mark.asyncio
async def test_only_active_is_fetched_trialing_and_past_due_are_excluded(provider):
    # Active-only by design: 1=Trialing, 3=PastDue, 5=Expired are all skipped.
    provider.collection.docs = [
        _usage_doc(meter_key="trialing", status=1),
        _usage_doc(meter_key="past-due", status=3),
        _usage_doc(meter_key="expired", status=5),
        _usage_doc(meter_key="active", status=2),
    ]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert [r.meter_key for r in result] == ["active"]


@pytest.mark.asyncio
async def test_expired_period_row_is_excluded(provider):
    doc = _usage_doc()
    doc["PeriodStartUtc"] = datetime.utcnow() - timedelta(days=60)
    doc["PeriodEndUtc"] = datetime.utcnow() - timedelta(days=30)
    provider.collection.docs = [doc]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result == []


@pytest.mark.asyncio
async def test_multiple_meters_all_returned(provider):
    provider.collection.docs = [_usage_doc(meter_key="tkn"), _usage_doc(meter_key="messages")]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert {r.meter_key for r in result} == {"tkn", "messages"}


@pytest.mark.asyncio
async def test_missing_numeric_fields_default_to_zero(provider):
    provider.collection.docs = [{
        "TenantId": "t1", "OrganizationId": "default", "SubscriptionStatus": 2, "MeterKey": "tkn",
        "PeriodStartUtc": datetime.utcnow() - timedelta(days=1),
        "PeriodEndUtc": datetime.utcnow() + timedelta(days=29),
    }]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].used == 0
    assert result[0].remaining == 0
    assert result[0].overage == 0


@pytest.mark.asyncio
async def test_db_error_returns_none(provider):
    with patch(REPO + "get_current_usage_docs", side_effect=ConnectionError("down")):
        result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result is None
