"""blocks_genesis._subscription.usage_service -- the entitlements read and the snapshot
that carries an organization's side, a member's side and the plan terms behind each.

Backed by an in-memory fake collection standing in for pymongo, the way the usage tests
are: these hold the filter shape, the mapping and the failure paths, not Mongo.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from bson import Decimal128

from blocks_genesis._subscription.enums import EntitlementLimitKind
from blocks_genesis._subscription.models import SubscriptionEntitlements, UsageSnapshot
from blocks_genesis._subscription.repository import get_current_entitlement_docs
from blocks_genesis._subscription.usage_service import SubscriptionUsageService

REPO = "blocks_genesis._subscription.repository."

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
    return all(
        any(_matches(doc, branch) for branch in condition)
        if key == "$or"
        else _matches_condition(doc.get(key), condition)
        for key, condition in filt.items()
    )


class _FakeCursor(list):
    """A list that also answers .sort(), the way a pymongo cursor does."""

    def sort(self, field, direction=1):
        return _FakeCursor(
            sorted(self, key=lambda d: d.get(field) or 0, reverse=direction < 0)
        )


class _FakeCollection:
    def __init__(self):
        self.docs = []
        self.last_filter = None

    def find(self, filt):
        self.last_filter = filt
        return _FakeCursor(d for d in self.docs if _matches(d, filt))


class _FakeProvider:
    """One collection per name, so a snapshot's usage and entitlement reads stay apart."""

    def __init__(self):
        self.collections = {}
        self.get_collection = AsyncMock(side_effect=self._get)

    async def _get(self, name, tenant_id=None):
        return self.collections.setdefault(name, _FakeCollection())

    def usage(self):
        return self.collections.setdefault("SubscriptionUsageCurrent", _FakeCollection())

    def entitlements(self):
        return self.collections.setdefault(
            "SubscriptionEntitlementsCurrent", _FakeCollection()
        )


@pytest.fixture
def provider():
    fake = _FakeProvider()
    with patch(REPO + "DbContext") as mock_db_context:
        mock_db_context.get_provider.return_value = fake
        yield fake


def _entitlement_doc(
    *,
    tenant_id="t1",
    organization_id="default",
    status=3,
    plan_code="team-plan",
    version=1,
    user_id=None,
    keys=("ai-credits", "frontier-models"),
):
    return {
        "_id": "sub-1",
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "UserId": user_id,
        "SubscriptionId": "sub-1",
        "SubscriptionStatus": status,
        "PlanId": "plan-1",
        "PlanCode": plan_code,
        "SubscriptionVersion": version,
        "SchemaVersion": 2,
        "CurrentPeriodEndUtc": datetime.now(timezone.utc) + timedelta(days=7),
        "Entitlements": [
            {
                "Key": key,
                "LimitKind": int(EntitlementLimitKind.UNLIMITED),
                "Limit": None,
                "MeterKey": key,
                "UnitLabel": "credit",
            }
            for key in keys
        ],
    }


def _usage_doc(*, meter_key="ai-credits", user_id=None, used=10, remaining=90):
    now = datetime.now(timezone.utc)
    return {
        "TenantId": "t1",
        "OrganizationId": "default",
        "UserId": user_id,
        "SubscriptionStatus": 3,
        "MeterKey": meter_key,
        "PeriodStartUtc": now - timedelta(days=1),
        "PeriodEndUtc": now + timedelta(days=29),
        "Included": 100,
        "Used": used,
        "Remaining": remaining,
        "Overage": 0,
        "OverageAllowed": True,
    }


# ---------------- the entitlements read ----------------


@pytest.mark.asyncio
async def test_requires_tenant_and_org():
    assert await SubscriptionUsageService.get_entitlements_current(
        tenant_id="", organization_id="default"
    ) is None
    assert await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id=""
    ) is None


@pytest.mark.asyncio
async def test_no_row_is_no_subscription_not_a_failed_read(provider):
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert isinstance(result, SubscriptionEntitlements)
    assert result.has_subscription is False
    assert result.entitlements == []


@pytest.mark.asyncio
async def test_maps_a_real_row(provider):
    provider.entitlements().docs = [_entitlement_doc()]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.has_subscription is True
    assert result.subscription_id == "sub-1"
    assert result.plan_id == "plan-1"
    assert result.plan_code == "team-plan"
    assert result.keys() == ["ai-credits", "frontier-models"]
    assert result.grants("ai-credits") is True
    assert result.grants("nothing-like-it") is False


@pytest.mark.asyncio
async def test_an_unlimited_entitlement_carries_no_cap(provider):
    provider.entitlements().docs = [_entitlement_doc(keys=("ai-credits",))]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    entitlement = result.entitlements[0]
    assert entitlement.limit_kind == int(EntitlementLimitKind.UNLIMITED)
    assert entitlement.limit is None
    assert entitlement.meter_key == "ai-credits"
    assert entitlement.unit_label == "credit"


@pytest.mark.asyncio
async def test_a_counted_cap_reads_as_a_number_whatever_mongo_stored(provider):
    doc = _entitlement_doc(keys=("ai-knowledge-folders",))
    doc["Entitlements"][0]["LimitKind"] = int(EntitlementLimitKind.COUNT)
    doc["Entitlements"][0]["Limit"] = Decimal128("25")
    provider.entitlements().docs = [doc]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.entitlements[0].limit == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_an_entry_with_no_key_is_dropped(provider):
    doc = _entitlement_doc(keys=("ai-credits",))
    doc["Entitlements"].append({"Key": "", "LimitKind": 0})
    provider.entitlements().docs = [doc]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.keys() == ["ai-credits"]


@pytest.mark.asyncio
async def test_a_trial_is_live_alongside_active(provider):
    provider.entitlements().docs = [_entitlement_doc(status=2, plan_code="trial-plan")]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.plan_code == "trial-plan"


@pytest.mark.asyncio
async def test_a_cancelled_subscription_grants_nothing(provider):
    provider.entitlements().docs = [_entitlement_doc(status=6)]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.has_subscription is False


@pytest.mark.asyncio
async def test_another_organizations_plan_does_not_leak(provider):
    provider.entitlements().docs = [_entitlement_doc(organization_id="other-org")]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.has_subscription is False


@pytest.mark.asyncio
async def test_the_newest_version_wins_while_a_plan_change_lands(provider):
    # Both rows are live for the moment between publishing the new plan and retiring the old.
    provider.entitlements().docs = [
        _entitlement_doc(plan_code="old-plan", version=1),
        _entitlement_doc(plan_code="new-plan", version=2),
    ]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.plan_code == "new-plan"


@pytest.mark.asyncio
async def test_a_db_error_reads_as_none_not_as_no_subscription(provider):
    with patch(REPO + "get_current_entitlement_docs", side_effect=ConnectionError("down")):
        result = await SubscriptionUsageService.get_entitlements_current(
            tenant_id="t1", organization_id="default"
        )
    assert result is None


# ---------------- whose plan terms ----------------


@pytest.mark.asyncio
async def test_a_member_is_granted_by_the_organizations_plan_when_they_have_no_own_row(provider):
    provider.entitlements().docs = [_entitlement_doc(plan_code="team-plan")]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    assert result.plan_code == "team-plan"


@pytest.mark.asyncio
async def test_a_members_own_row_wins_over_the_organizations_once_published(provider):
    provider.entitlements().docs = [
        _entitlement_doc(plan_code="team-plan"),
        _entitlement_doc(plan_code="seat-plan", user_id="u1"),
    ]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    assert result.plan_code == "seat-plan"


@pytest.mark.asyncio
async def test_the_organization_read_never_picks_a_members_row(provider):
    provider.entitlements().docs = [_entitlement_doc(plan_code="seat-plan", user_id="u1")]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.has_subscription is False


@pytest.mark.asyncio
async def test_one_members_row_does_not_answer_for_another(provider):
    provider.entitlements().docs = [
        _entitlement_doc(plan_code="team-plan"),
        _entitlement_doc(plan_code="seat-plan", user_id="u1"),
    ]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default", user_id="u2"
    )
    assert result.plan_code == "team-plan"


# ---------------- the snapshot: three answers in one object ----------------


@pytest.mark.asyncio
async def test_the_snapshot_carries_the_organization_the_member_and_both_plans(provider):
    provider.usage().docs = [
        _usage_doc(used=10, remaining=90),
        _usage_doc(used=4, remaining=96, user_id="u1"),
    ]
    provider.entitlements().docs = [_entitlement_doc(plan_code="team-plan")]

    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )

    assert [r.used for r in snapshot.organization] == [pytest.approx(10)]
    assert [r.used for r in snapshot.member["u1"]] == [pytest.approx(4)]
    assert snapshot.rows_for("u1") == snapshot.member["u1"]
    assert snapshot.entitlements.organization.plan_code == "team-plan"
    assert snapshot.entitlements.member["u1"].plan_code == "team-plan"


@pytest.mark.asyncio
async def test_without_a_user_id_the_members_side_is_left_unasked(provider):
    provider.usage().docs = [_usage_doc(), _usage_doc(user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]

    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default"
    )

    assert len(snapshot.organization) == 1
    # Absent, not empty: nobody was asked about, which is not "asked and found nothing".
    assert snapshot.member == {}
    assert snapshot.rows_for("u1") is None
    assert snapshot.entitlements.organization.has_subscription is True
    assert snapshot.entitlements.member == {}


@pytest.mark.asyncio
async def test_a_member_with_no_rows_reads_empty_while_the_organization_still_has_its_own(provider):
    provider.usage().docs = [_usage_doc()]
    provider.entitlements().docs = [_entitlement_doc()]

    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )

    assert len(snapshot.organization) == 1
    assert snapshot.member == {"u1": []}


@pytest.mark.asyncio
async def test_no_live_subscription_is_empty_rows_not_a_failed_read(provider):
    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    assert snapshot.organization == []
    assert snapshot.member == {"u1": []}
    assert snapshot.entitlements.organization.has_subscription is False
    assert snapshot.entitlements.member["u1"].has_subscription is False


@pytest.mark.asyncio
async def test_a_failed_usage_read_leaves_the_rows_none_and_the_plan_intact(provider):
    provider.entitlements().docs = [_entitlement_doc()]
    with patch(REPO + "get_scoped_usage_docs", side_effect=ConnectionError("down")):
        snapshot = await SubscriptionUsageService.get_usage_snapshot(
            tenant_id="t1", organization_id="default", user_id="u1"
        )
    assert snapshot.organization is None
    # The key is there because the member was asked about; None is the failure.
    assert snapshot.member == {"u1": None}
    assert snapshot.entitlements.organization.has_subscription is True


@pytest.mark.asyncio
async def test_a_failed_plan_read_leaves_the_rows_intact(provider):
    provider.usage().docs = [_usage_doc()]
    with patch(REPO + "get_current_entitlement_docs", side_effect=ConnectionError("down")):
        snapshot = await SubscriptionUsageService.get_usage_snapshot(
            tenant_id="t1", organization_id="default"
        )
    assert len(snapshot.organization) == 1
    assert snapshot.entitlements.organization is None
    assert snapshot.entitlements.member == {}


@pytest.mark.asyncio
async def test_a_snapshot_without_ids_answers_nothing_rather_than_raising(provider):
    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="", organization_id="default", user_id="u1"
    )
    assert snapshot.organization is None
    assert snapshot.member == {"u1": None}
    assert snapshot.entitlements.organization is None
    assert snapshot.entitlements.member == {"u1": None}


@pytest.mark.asyncio
async def test_a_snapshot_reads_each_collection_once_however_many_sides(provider):
    provider.entitlements().docs = [_entitlement_doc()]
    await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    names = [call.args[0] for call in provider.get_collection.await_args_list]
    assert names.count("SubscriptionEntitlementsCurrent") == 1
    # And the balances once too: every side lives in one collection under one filter,
    # so asking per side would be the same query run twice.
    assert names.count("SubscriptionUsageCurrent") == 1


@pytest.mark.asyncio
async def test_the_plan_read_is_scoped_to_the_tenant_org_and_live_statuses(provider):
    await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    filt = provider.entitlements().last_filter
    assert filt["TenantId"] == "t1"
    assert filt["OrganizationId"] == "default"
    # The literals are the point: they pin the wire values blocks-utilities writes for
    # Active and Trialing, so a wrong enum mapping cannot make this test agree with it.
    assert [branch["SubscriptionStatus"] for branch in filt["$or"]] == [3, 2]
    assert "$gt" in filt["$or"][1]["CurrentPeriodEndUtc"]


@pytest.mark.asyncio
async def test_a_trialing_plan_stops_counting_once_the_trial_has_ended(provider):
    ended = _entitlement_doc(status=2)
    ended["CurrentPeriodEndUtc"] = datetime.now(timezone.utc) - timedelta(hours=1)
    provider.entitlements().docs = [ended]
    assert await get_current_entitlement_docs("t1", "default") == []


@pytest.mark.asyncio
async def test_a_trialing_plan_counts_while_the_trial_runs(provider):
    provider.entitlements().docs = [_entitlement_doc(status=2)]
    assert len(await get_current_entitlement_docs("t1", "default")) == 1


@pytest.mark.asyncio
async def test_an_active_plan_is_not_cut_off_by_a_late_renewal(provider):
    late = _entitlement_doc(status=3)
    late["CurrentPeriodEndUtc"] = datetime.now(timezone.utc) - timedelta(hours=1)
    provider.entitlements().docs = [late]
    assert len(await get_current_entitlement_docs("t1", "default")) == 1


@pytest.mark.asyncio
async def test_an_unreadable_cap_is_no_cap_rather_than_a_cap_of_zero(provider):
    doc = _entitlement_doc(keys=("ai-knowledge-folders",))
    doc["Entitlements"][0]["LimitKind"] = int(EntitlementLimitKind.COUNT)
    doc["Entitlements"][0]["Limit"] = "not-a-number"
    provider.entitlements().docs = [doc]
    result = await SubscriptionUsageService.get_entitlements_current(
        tenant_id="t1", organization_id="default"
    )
    assert result.entitlements[0].limit is None


@pytest.mark.asyncio
async def test_ensure_member_reads_a_member_the_snapshot_never_asked_about(provider):
    """The member a snapshot was built for is not always the one that answers."""
    provider.usage().docs = [_usage_doc(used=10, remaining=90), _usage_doc(used=4, remaining=96, user_id="u2")]
    provider.entitlements().docs = [_entitlement_doc(plan_code="team-plan")]
    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default"
    )
    assert snapshot.member == {}

    rows = await SubscriptionUsageService.ensure_member(
        snapshot, tenant_id="t1", organization_id="default", user_id="u2"
    )

    assert [r.used for r in rows] == [pytest.approx(4)]
    assert [r.used for r in snapshot.member["u2"]] == [pytest.approx(4)]
    assert snapshot.entitlements.member["u2"].plan_code == "team-plan"
    assert [r.used for r in snapshot.organization] == [pytest.approx(10)]


@pytest.mark.asyncio
async def test_ensure_member_does_not_read_a_member_already_answered_for(provider):
    provider.usage().docs = [_usage_doc(), _usage_doc(used=4, remaining=96, user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]
    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )

    with patch(REPO + "get_current_usage_docs", side_effect=AssertionError("read again")):
        rows = await SubscriptionUsageService.ensure_member(
            snapshot, tenant_id="t1", organization_id="default", user_id="u1"
        )

    assert [r.used for r in rows] == [pytest.approx(4)]


@pytest.mark.asyncio
async def test_ensure_member_keeps_an_empty_answer_rather_than_reading_again(provider):
    """[] is an answer -- that member has no rows -- so it is not a miss."""
    provider.usage().docs = [_usage_doc()]
    provider.entitlements().docs = [_entitlement_doc()]
    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )
    assert snapshot.member == {"u1": []}

    with patch(REPO + "get_current_usage_docs", side_effect=AssertionError("read again")):
        rows = await SubscriptionUsageService.ensure_member(
            snapshot, tenant_id="t1", organization_id="default", user_id="u1"
        )

    assert rows == []


@pytest.mark.asyncio
async def test_ensure_member_retries_a_member_whose_read_failed(provider):
    """None is a failure, not an answer, so it is worth asking again."""
    provider.usage().docs = [_usage_doc(used=4, remaining=96, user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]
    snapshot = UsageSnapshot(organization=[], member={"u1": None})

    rows = await SubscriptionUsageService.ensure_member(
        snapshot, tenant_id="t1", organization_id="default", user_id="u1"
    )

    assert [r.used for r in rows] == [pytest.approx(4)]


@pytest.mark.asyncio
async def test_ensure_member_can_read_the_usage_alone(provider):
    provider.usage().docs = [_usage_doc(used=4, remaining=96, user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]
    snapshot = UsageSnapshot(organization=[])

    rows = await SubscriptionUsageService.ensure_member(
        snapshot, tenant_id="t1", organization_id="default", user_id="u1", entitlements=False
    )

    assert [r.used for r in rows] == [pytest.approx(4)]
    assert snapshot.entitlements.member == {}


@pytest.mark.asyncio
async def test_ensure_member_can_read_the_plan_alone(provider):
    provider.entitlements().docs = [_entitlement_doc(plan_code="team-plan")]
    snapshot = UsageSnapshot(organization=[])

    with patch(REPO + "get_current_usage_docs", side_effect=AssertionError("usage was read")):
        rows = await SubscriptionUsageService.ensure_member(
            snapshot, tenant_id="t1", organization_id="default", user_id="u1", usage=False
        )

    assert rows is None
    assert snapshot.member == {}
    assert snapshot.entitlements.member["u1"].plan_code == "team-plan"


@pytest.mark.asyncio
async def test_one_read_is_split_into_the_organization_and_each_member(provider):
    """The sides are told apart by UserId after the fact, not by asking separately."""
    provider.usage().docs = [
        _usage_doc(used=10, remaining=90),
        _usage_doc(used=4, remaining=96, user_id="u1"),
        _usage_doc(used=7, remaining=93, user_id="u2"),
    ]
    provider.entitlements().docs = [_entitlement_doc()]

    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )

    assert [r.used for r in snapshot.organization] == [pytest.approx(10)]
    assert [r.used for r in snapshot.member["u1"]] == [pytest.approx(4)]
    # u2 was never asked about, so their row is not in the snapshot at all.
    assert "u2" not in snapshot.member


@pytest.mark.asyncio
async def test_an_organization_row_storing_an_empty_user_id_lands_on_the_organization(provider):
    """What the live collection holds. Splitting on truthiness covers empty, null and absent."""
    empty, absent = _usage_doc(used=1, remaining=99), _usage_doc(used=2, remaining=98)
    empty["UserId"] = ""
    del absent["UserId"]
    provider.usage().docs = [empty, absent, _usage_doc(used=4, remaining=96, user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]

    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1"
    )

    assert sorted(r.used for r in snapshot.organization) == [pytest.approx(1), pytest.approx(2)]
    assert [r.used for r in snapshot.member["u1"]] == [pytest.approx(4)]


@pytest.mark.asyncio
async def test_get_members_answers_a_list_of_ids_without_a_snapshot(provider):
    provider.usage().docs = [
        _usage_doc(used=10, remaining=90),
        _usage_doc(used=4, remaining=96, user_id="u1"),
        _usage_doc(used=7, remaining=93, user_id="u2"),
    ]
    provider.entitlements().docs = [_entitlement_doc(plan_code="team-plan")]

    sides = await SubscriptionUsageService.get_members(
        tenant_id="t1", organization_id="default", user_ids=["u1", "u2", "u3"]
    )

    assert [r.used for r in sides.usage["u1"]] == [pytest.approx(4)]
    assert [r.used for r in sides.usage["u2"]] == [pytest.approx(7)]
    assert sides.usage["u3"] == []
    assert sides.entitlements["u1"].plan_code == "team-plan"


@pytest.mark.asyncio
async def test_get_members_reads_each_collection_once_however_long_the_list(provider):
    provider.usage().docs = [_usage_doc(user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]

    await SubscriptionUsageService.get_members(
        tenant_id="t1", organization_id="default", user_ids=["u1", "u2", "u3", "u4"]
    )

    names = [call.args[0] for call in provider.get_collection.await_args_list]
    assert names.count("SubscriptionUsageCurrent") == 1
    assert names.count("SubscriptionEntitlementsCurrent") == 1


@pytest.mark.asyncio
async def test_get_members_never_reads_the_organizations_rows(provider):
    provider.usage().docs = [_usage_doc(used=10, remaining=90), _usage_doc(used=4, remaining=96, user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]

    sides = await SubscriptionUsageService.get_members(
        tenant_id="t1", organization_id="default", user_ids=["u1"], entitlements=False
    )

    assert [r.used for r in sides.usage["u1"]] == [pytest.approx(4)]
    assert provider.usage().last_filter["UserId"] == {"$in": ["u1"]}
    assert sides.entitlements == {}


@pytest.mark.asyncio
async def test_get_members_with_no_ids_reads_nothing(provider):
    sides = await SubscriptionUsageService.get_members(
        tenant_id="t1", organization_id="default", user_ids=[]
    )

    assert sides.usage == {} and sides.entitlements == {}
    assert provider.get_collection.await_args_list == []


@pytest.mark.asyncio
async def test_a_snapshot_can_leave_the_plan_unread(provider):
    """The gate asks on every request and needs balances only, so it skips the plan read."""
    provider.usage().docs = [_usage_doc(used=10, remaining=90), _usage_doc(used=4, remaining=96, user_id="u1")]
    provider.entitlements().docs = [_entitlement_doc()]

    snapshot = await SubscriptionUsageService.get_usage_snapshot(
        tenant_id="t1", organization_id="default", user_id="u1", entitlements=False
    )

    assert [r.used for r in snapshot.member["u1"]] == [pytest.approx(4)]
    assert snapshot.entitlements.organization is None
    names = [call.args[0] for call in provider.get_collection.await_args_list]
    assert names == ["SubscriptionUsageCurrent"]
