"""Direct Mongo access to the subscription usage collections: Subscriptions,
SubscriptionUsageCounters.
"""
import logging
from typing import Any, Dict, Optional

from blocks_genesis._database.db_context import DbContext
from blocks_genesis._subscription import period as _period

logger = logging.getLogger(__name__)

_SUBSCRIPTIONS = "Subscriptions"
_USAGE_COUNTERS = "SubscriptionUsageCounters"


async def _collection(name: str, tenant_id: str):
    provider = DbContext.get_provider()
    return await provider.get_collection(name, tenant_id)


def _parse_meter(doc: Dict[str, Any]) -> _period.Meter:
    return _period.Meter(
        meter_key=doc.get("MeterKey") or "",
        reset_policy=doc.get("ResetPolicy", 0),
        included_quantity=doc.get("IncludedQuantity") or 0,
        carry_forward_cap=doc.get("CarryForwardCap"),
        overage_allowed=doc.get("OverageAllowed", True),
    )


def _parse_trial(doc: Optional[Dict[str, Any]]) -> Optional[_period.Trial]:
    if not doc:
        return None
    grants = [
        _period.TrialGrant(meter_key=g.get("MeterKey") or "", included_quantity=g.get("IncludedQuantity") or 0)
        for g in doc.get("Grants") or []
    ]
    return _period.Trial(ends_at_utc=doc.get("EndsAtUtc"), grants=grants)


def _parse_schedule(doc: Optional[Dict[str, Any]]) -> Optional[_period.Schedule]:
    if not doc or not doc.get("AnchorInstantUtc"):
        return None
    return _period.Schedule(
        interval=doc.get("Interval", _period.INTERVAL_MONTH),
        interval_count=doc.get("IntervalCount") or 1,
        anchor_instant_utc=doc["AnchorInstantUtc"],
        time_zone_id=doc.get("TimeZoneId") or "UTC",
        anchor_day_of_month=doc.get("AnchorDayOfMonth") or 1,
        anchor_minutes_from_midnight=doc.get("AnchorMinutesFromMidnight") or 0,
    )


def parse_subscription(doc: Dict[str, Any]) -> _period.Subscription:
    plan = doc.get("Plan") or {}
    return _period.Subscription(
        item_id=str(doc.get("_id") or ""),
        tenant_id=doc.get("TenantId") or "",
        organization_id=doc.get("OrganizationId") or "",
        status=doc.get("Status", 0),
        created_at_utc=doc.get("CreatedAtUtc") or _period.now_utc(),
        usage_schedule=_parse_schedule(doc.get("UsageSchedule")),
        trial=_parse_trial(doc.get("Trial")),
        meters=[_parse_meter(m) for m in plan.get("Meters") or []],
    )


async def get_live_subscription(tenant_id: str, organization_id: str) -> Optional[_period.Subscription]:
    collection = await _collection(_SUBSCRIPTIONS, tenant_id)
    doc = collection.find_one({
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "Status": {"$in": list(_period.LIVE_STATUSES)},
    })
    return parse_subscription(doc) if doc else None


def counter_id(subscription_id: str, meter_key: str, period_key: str) -> str:
    return f"{subscription_id}:{meter_key}:{period_key}"


async def get_counter(tenant_id: str, counter_id_: str) -> Optional[Dict[str, Any]]:
    collection = await _collection(_USAGE_COUNTERS, tenant_id)
    return collection.find_one({"_id": counter_id_})
