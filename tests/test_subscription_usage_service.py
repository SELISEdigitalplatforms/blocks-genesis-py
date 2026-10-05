"""blocks_genesis._subscription.usage_service.SubscriptionUsageService.

Reads the SubscriptionUsageCurrent model straight from Mongo. Backed by an in-memory fake
collection standing in for pymongo -- these test the mapping and failure paths, not Mongo.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from blocks_genesis._subscription.usage_service import SubscriptionUsageService
from blocks_genesis._subscription.enums import SubLimitBehaviour, UsageWindow
from blocks_genesis._subscription.models import UsageResult

REPO = "blocks_genesis._subscription.repository."


class _FakeCollection:
    def __init__(self):
        self.docs = []
        self.last_filter = None

    def find(self, filt):
        self.last_filter = filt
        return [d for d in self.docs if _matches(d, filt)]


_OPERATORS = {
    "$in": lambda actual, expected: actual in expected,
    "$lte": lambda actual, expected: actual is not None and actual <= expected,
    "$gt": lambda actual, expected: actual is not None and actual > expected,
}


def _matches_condition(actual, condition):
    if not isinstance(condition, dict):
        return actual == condition
    return all(
        _OPERATORS[operator](actual, expected)
        for operator, expected in condition.items()
        if operator in _OPERATORS
    )


def _matches(doc, filt):
    return all(_matches_condition(doc.get(key), condition) for key, condition in filt.items())


class _FakeProvider:
    def __init__(self):
        self.collection = _FakeCollection()
        self.get_collection = AsyncMock(return_value=self.collection)


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
    status=3,
    included=500,
    used=800,
    remaining=0,
    overage=300,
    overage_allowed=True,
    user_id=None,
    scale=0,
):
    now = datetime.now(timezone.utc)
    doc_id = f"sub-1:{meter_key}:M20260902T024500Z"
    if user_id:
        doc_id = f"{doc_id}:{user_id}"
    return {
        "_id": doc_id,
        "UserId": user_id,
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "SubscriptionId": "sub-1",
        "SubscriptionStatus": status,
        "MeterKey": meter_key,
        "UnitLabel": "token",
        "PeriodKey": "M20260902T024500Z",
        "PeriodStartUtc": now - timedelta(days=1),
        "PeriodEndUtc": now + timedelta(days=29),
        "QuantityScale": scale,
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
    assert row.used == pytest.approx(800)
    assert row.remaining == pytest.approx(0)
    assert row.overage == pytest.approx(300)
    assert row.allowed is True  # over the included 500, but OverageAllowed


@pytest.mark.asyncio
async def test_over_allowance_without_overage_is_not_allowed(provider):
    provider.collection.docs = [_usage_doc(used=800, included=500, overage_allowed=False)]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is False


@pytest.mark.asyncio
async def test_allowance_used_up_without_overage_is_not_allowed(provider):
    provider.collection.docs = [_usage_doc(used=500, included=500, remaining=0, overage=0, overage_allowed=False)]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "used, included, scale, allowed",
    [
        (499.9999999, 500, 2, False),  # rounds to 500.00, so spent
        (499.99, 500, 2, True),  # a whole cent is still left
        (500.004, 500, 2, False),
        (499.5, 500, 0, True),  # whole-number meter compares as is
        (499, 500, 0, True),
        (500, 500, 0, False),
    ],
)
async def test_room_is_compared_at_the_meters_precision(provider, used, included, scale, allowed):
    provider.collection.docs = [
        _usage_doc(used=used, included=included, remaining=0, overage=0, overage_allowed=False, scale=scale)
    ]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is allowed


@pytest.mark.asyncio
async def test_allowance_used_up_with_overage_is_allowed(provider):
    provider.collection.docs = [_usage_doc(used=500, included=500, remaining=0, overage=0, overage_allowed=True)]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is True


@pytest.mark.asyncio
async def test_within_allowance_is_allowed(provider):
    provider.collection.docs = [_usage_doc(used=100, included=500, remaining=400, overage=0, overage_allowed=False)]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].allowed is True
    assert result[0].remaining == pytest.approx(400)


@pytest.mark.asyncio
async def test_the_overage_flag_is_carried_so_a_caller_can_cap_a_charge(provider):
    provider.collection.docs = [
        _usage_doc(meter_key="capped", overage_allowed=False),
        _usage_doc(meter_key="open", overage_allowed=True),
    ]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    flags = {row.meter_key: row.overage_allowed for row in result}
    assert flags == {"capped": False, "open": True}


@pytest.mark.asyncio
async def test_pace_limits_are_read_off_the_row(provider):
    """2000 credits a month, 100 a day, 60 spent today."""
    start = datetime(2026, 9, 29, tzinfo=timezone.utc)
    doc = _usage_doc(meter_key="ai-credits")
    doc["SubLimits"] = [
        {
            "Window": 1, "WindowCount": 1, "Rolling": False, "Behaviour": 0,
            "Quantity": 100, "Used": 60, "Remaining": 40, "Exceeded": False,
            "WindowStartUtc": start, "WindowEndUtc": start + timedelta(days=1),
        }
    ]
    provider.collection.docs = [doc, _usage_doc(meter_key="no-pace")]

    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    by_key = {row.meter_key: row for row in result}

    [day] = by_key["ai-credits"].sub_limits
    assert (day.window, day.behaviour) == (UsageWindow.DAY, SubLimitBehaviour.REFUSE)
    assert (day.quantity, day.used, day.remaining, day.exceeded) == (100, 60, 40, False)
    assert day.window_end_utc == start + timedelta(days=1)
    assert by_key["no-pace"].sub_limits == []


@pytest.mark.asyncio
async def test_filters_by_tenant_org_active_status_and_current_period(provider):
    provider.collection.docs = [_usage_doc()]
    await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    filt = provider.collection.last_filter
    assert filt["TenantId"] == "t1"
    assert filt["OrganizationId"] == "default"
    # The literal is the point: it pins the wire value blocks-utilities writes for Active.
    # Deriving it from the enum here would make this test agree with any mapping, including a
    # wrong one -- which is exactly the drift that made every active row invisible.
    assert filt["SubscriptionStatus"] == 3
    assert "$lte" in filt["PeriodStartUtc"]
    assert "$gt" in filt["PeriodEndUtc"]


@pytest.mark.asyncio
async def test_cancelled_subscription_row_is_excluded(provider):
    provider.collection.docs = [_usage_doc(status=6)]  # Canceled
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result == []


@pytest.mark.asyncio
async def test_only_active_is_fetched_trialing_and_past_due_are_excluded(provider):
    # Active-only by design: 2=Trialing, 4=PastDue, 5=Unpaid are all skipped.
    provider.collection.docs = [
        _usage_doc(meter_key="trialing", status=2),
        _usage_doc(meter_key="past-due", status=4),
        _usage_doc(meter_key="unpaid", status=5),
        _usage_doc(meter_key="active", status=3),
    ]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert [r.meter_key for r in result] == ["active"]


@pytest.mark.asyncio
async def test_expired_period_row_is_excluded(provider):
    doc = _usage_doc()
    doc["PeriodStartUtc"] = datetime.now(timezone.utc) - timedelta(days=60)
    doc["PeriodEndUtc"] = datetime.now(timezone.utc) - timedelta(days=30)
    provider.collection.docs = [doc]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result == []


@pytest.mark.asyncio
async def test_multiple_meters_all_returned(provider):
    provider.collection.docs = [_usage_doc(meter_key="tkn"), _usage_doc(meter_key="second-meter")]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert {r.meter_key for r in result} == {"tkn", "second-meter"}


@pytest.mark.asyncio
async def test_missing_numeric_fields_default_to_zero(provider):
    provider.collection.docs = [{
        "TenantId": "t1", "OrganizationId": "default", "SubscriptionStatus": 3, "MeterKey": "tkn",
        "PeriodStartUtc": datetime.now(timezone.utc) - timedelta(days=1),
        "PeriodEndUtc": datetime.now(timezone.utc) + timedelta(days=29),
    }]
    result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result[0].used == pytest.approx(0)
    assert result[0].remaining == pytest.approx(0)
    assert result[0].overage == pytest.approx(0)


@pytest.mark.asyncio
async def test_db_error_returns_none(provider):
    with patch(REPO + "get_current_usage_docs", side_effect=ConnectionError("down")):
        result = await SubscriptionUsageService.get_usage_current(tenant_id="t1", organization_id="default")
    assert result is None


# ---------------- fractional / mixed numeric types ----------------


def test_number_reads_int64_double_and_decimal128_alike():
    from bson import Decimal128, Int64

    from blocks_genesis._subscription.usage_service import _number

    assert _number(Int64(800)) == pytest.approx(800.0)
    assert _number(41.2) == pytest.approx(41.2)
    assert _number(Decimal128("41.2")) == pytest.approx(41.2)
    assert _number(None) == pytest.approx(0.0)
    assert _number("not-a-number") == pytest.approx(0.0)


def test_to_result_reads_a_decimal128_row_as_the_meter_writes_it_now():
    # The live shape: Included/Used/Remaining/Overage as $numberDecimal.
    from bson import Decimal128

    from blocks_genesis._subscription.usage_service import _to_result

    result = _to_result({
        "MeterKey": "tkn",
        "Included": Decimal128("550.55"),
        "Used": Decimal128("7.5"),
        "Remaining": Decimal128("543.05"),
        "Overage": Decimal128("0"),
        "OverageAllowed": True,
    })
    assert result.used == pytest.approx(7.5)
    assert result.remaining == pytest.approx(543.05)
    assert result.overage == pytest.approx(0.0)
    assert result.allowed is True


def test_to_result_keeps_fractional_quantities_intact():
    from blocks_genesis._subscription.usage_service import _to_result

    result = _to_result({
        "MeterKey": "ai-credits",
        "Used": 800.75,
        "Included": 1000,
        "Remaining": 199.25,
        "Overage": 0.5,
        "OverageAllowed": False,
    })
    assert result.used == pytest.approx(800.75)
    assert result.remaining == pytest.approx(199.25)
    assert result.overage == pytest.approx(0.5)
    assert result.allowed is True


def test_to_result_int64_document_still_works():
    # The shape in Mongo today: Used/Remaining/Overage as $numberLong.
    from bson import Int64

    from blocks_genesis._subscription.usage_service import _to_result

    result = _to_result({
        "MeterKey": "ai-credits",
        "Used": Int64(800),
        "Included": Int64(500),
        "Remaining": Int64(0),
        "Overage": Int64(300),
        "OverageAllowed": False,
    })
    assert result.used == pytest.approx(800.0)
    assert result.remaining == pytest.approx(0.0)
    assert result.overage == pytest.approx(300.0)
    # 800 used against 500 included with no overage allowed -- exhausted.
    assert result.allowed is False


# ---------------- QuantityScale ----------------


def test_to_result_reads_the_quantity_scale():
    from blocks_genesis._subscription.usage_service import _to_result

    # 0 is whole numbers only; 2 allows 550.55.
    assert _to_result({"MeterKey": "m", "QuantityScale": 0}).quantity_scale == 0
    assert _to_result({"MeterKey": "m", "QuantityScale": 2}).quantity_scale == 2


def test_is_fraction_allowed_is_derived_from_the_scale():
    from blocks_genesis._subscription.usage_service import _to_result

    assert _to_result({"MeterKey": "m", "QuantityScale": 0}).is_fraction_allowed is False
    assert _to_result({"MeterKey": "m", "QuantityScale": 1}).is_fraction_allowed is True
    assert _to_result({"MeterKey": "m", "QuantityScale": 3}).is_fraction_allowed is True


def test_an_absent_or_unreadable_scale_is_whole_numbers_only():
    from blocks_genesis._subscription.usage_service import _to_result

    for doc in ({"MeterKey": "m"}, {"MeterKey": "m", "QuantityScale": None},
                {"MeterKey": "m", "QuantityScale": "abc"}):
        result = _to_result(doc)
        assert result.quantity_scale == 0
        assert result.is_fraction_allowed is False


def test_the_scale_is_clamped_to_the_api_maximum():
    from blocks_genesis._subscription.usage_service import MAX_QUANTITY_SCALE, _to_result

    assert MAX_QUANTITY_SCALE == 6
    assert _to_result({"MeterKey": "m", "QuantityScale": 9}).quantity_scale == 6
    assert _to_result({"MeterKey": "m", "QuantityScale": -2}).quantity_scale == 0


# ---------------- whose rows: the organization's, or one member's ----------------


@pytest.mark.asyncio
async def test_the_organization_read_leaves_out_every_member_row(provider):
    # The defect this scope split fixes: both kinds sit under the same OrganizationId, so
    # an unscoped read handed the caller one organization row and one row per member, all
    # for the same meter.
    provider.collection.docs = [
        _usage_doc(meter_key="ai-credits", used=10, remaining=90),
        _usage_doc(meter_key="ai-credits", used=7, remaining=93, user_id="u1"),
        _usage_doc(meter_key="ai-credits", used=3, remaining=97, user_id="u2"),
    ]
    result = await SubscriptionUsageService.get_usage_current(
        tenant_id="t1", organization_id="default"
    )
    assert len(result) == 1
    assert result[0].used == pytest.approx(10)


@pytest.mark.asyncio
async def test_a_row_written_before_the_user_field_existed_is_still_the_organizations(provider):
    doc = _usage_doc()
    del doc["UserId"]
    provider.collection.docs = [doc]
    result = await SubscriptionUsageService.get_usage_current(
        tenant_id="t1", organization_id="default"
    )
    assert len(result) == 1


@pytest.mark.asyncio
async def test_a_member_read_returns_only_that_members_rows(provider):
    provider.collection.docs = [
        _usage_doc(meter_key="ai-credits", used=10, remaining=90),
        _usage_doc(meter_key="ai-credits", used=7, remaining=93, user_id="u1"),
        _usage_doc(meter_key="ai-credits", used=3, remaining=97, user_id="u2"),
    ]
    result = await SubscriptionUsageService.get_usage_current(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    assert len(result) == 1
    assert result[0].used == pytest.approx(7)
    assert provider.collection.last_filter["UserId"] == "u1"


@pytest.mark.asyncio
async def test_a_member_with_no_rows_of_their_own_reads_empty_not_the_organizations(provider):
    provider.collection.docs = [_usage_doc(meter_key="ai-credits")]
    result = await SubscriptionUsageService.get_usage_current(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    assert result == []


@pytest.mark.asyncio
async def test_an_empty_user_id_reads_the_organization(provider):
    provider.collection.docs = [_usage_doc(), _usage_doc(user_id="u1")]
    result = await SubscriptionUsageService.get_usage_current(
        tenant_id="t1", organization_id="default", user_id=""
    )
    assert len(result) == 1
    assert provider.collection.last_filter["UserId"] == {"$in": [None, ""]}


@pytest.mark.asyncio
async def test_an_organization_row_storing_an_empty_user_id_is_still_read(provider):
    """What the live collection actually holds. Matching only on null returned nothing,
    which reads as no live subscription and refuses the whole organization."""
    doc = _usage_doc()
    doc["UserId"] = ""
    provider.collection.docs = [doc, _usage_doc(user_id="u1")]

    result = await SubscriptionUsageService.get_usage_current(
        tenant_id="t1", organization_id="default"
    )

    assert len(result) == 1
    assert result[0].used == pytest.approx(800)
