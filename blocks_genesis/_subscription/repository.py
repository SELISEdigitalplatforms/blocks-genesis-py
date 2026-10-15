"""Reads the subscription read models: SubscriptionUsageCurrent (a balance per meter) and
SubscriptionEntitlementsCurrent (what the plan behind those balances grants).

The collections are synchronous PyMongo, so each read, cursor walk included, runs on a
worker thread. On the event loop it would stall every request while the database answers.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from blocks_genesis._database.db_context import DbContext
from blocks_genesis._subscription.enums import SubscriptionStatus

logger = logging.getLogger(__name__)

_USAGE_CURRENT = "SubscriptionUsageCurrent"
_ENTITLEMENTS_CURRENT = "SubscriptionEntitlementsCurrent"
# Latest period first, a best-effort stand-in for "newest subscription first". During a
# plan change the old and new subscriptions are both live, and a reader taking the first
# row per meter should get the current plan's, not whichever Mongo returns first. This is
# right when the old subscription ends at the change: the new one opened the later period.
# It is not right when the old subscription renews during the overlap -- its new period
# starts later and sorts first. A true newest-subscription order needs a subscription start
# field on the usage read model (blocks-utilities); that is a follow-up. _id breaks a tie so
# the order never depends on storage.
_NEWEST_FIRST = [("PeriodStartUtc", -1), ("_id", -1)]

def _live_status(now: datetime) -> Dict[str, Any]:
    """Active rows, plus trialing rows whose trial has not ended.

    A trial grants its plan the same as a paid subscription does, so its entitlements and
    usage balances both count as live. A trial's usage window can outlast the trial itself, so
    a trialing row is also bounded by CurrentPeriodEndUtc. An active row is not: a renewal
    sweep that runs late must not cut off a paying subscriber.
    """
    return {"$or": [
        {"SubscriptionStatus": int(SubscriptionStatus.ACTIVE)},
        {
            "SubscriptionStatus": int(SubscriptionStatus.TRIALING),
            "CurrentPeriodEndUtc": {"$gt": now},
        },
    ]}


async def _collection(name: str, tenant_id: str):
    provider = DbContext.get_provider()
    return await provider.get_collection(name, tenant_id)


async def get_current_usage_docs(
    tenant_id: str, organization_id: str, user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """One row per meter for the current period. Active and trialing subscriptions.

    The collection holds the organization's own rows and one set per member side by side
    under the same OrganizationId, told apart only by UserId. Without a user_id this reads
    the organization's alone.

    An organization's row is matched on all three ways "nobody" has been written: the live
    data stores an empty string, older rows predate the field entirely, and null is the
    third shape the same idea can take. Matching only one of them returns nothing at all,
    which reads as "no live subscription" and refuses the whole organization.
    """
    collection = await _collection(_USAGE_CURRENT, tenant_id)
    now = datetime.now(timezone.utc)
    query = {
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "UserId": user_id if user_id else {"$in": [None, ""]},
        **_live_status(now),
        "PeriodStartUtc": {"$lte": now},
        "PeriodEndUtc": {"$gt": now},
    }
    return await asyncio.to_thread(lambda: list(collection.find(query, sort=_NEWEST_FIRST)))


async def get_scoped_usage_docs(
    tenant_id: str,
    organization_id: str,
    user_ids: Sequence[str] = (),
    include_organization: bool = True,
) -> List[Dict[str, Any]]:
    """The organization's rows and each named member's, in one query.

    Every side of a caller lives in this one collection under the same OrganizationId and
    differs only by UserId, so asking per side would be the same query run twice. One `$in`
    covers them all and the caller splits the answer by UserId.

    The organization is matched on all three ways "nobody" has been written: the live data
    stores an empty string, older rows predate the field entirely, and null is the third
    shape the same idea can take.
    """
    collection = await _collection(_USAGE_CURRENT, tenant_id)
    now = datetime.now(timezone.utc)
    owners: List[Any] = [None, ""] if include_organization else []
    owners.extend(u for u in user_ids if u)
    if not owners:
        return []
    query = {
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "UserId": {"$in": owners},
        **_live_status(now),
        "PeriodStartUtc": {"$lte": now},
        "PeriodEndUtc": {"$gt": now},
    }
    return await asyncio.to_thread(lambda: list(collection.find(query, sort=_NEWEST_FIRST)))


async def get_current_entitlement_docs(
    tenant_id: str, organization_id: str
) -> List[Dict[str, Any]]:
    """Every live subscription's plan terms for the organization, newest version first.

    Deliberately unscoped by user: one read serves the organization and any member, and
    the caller picks whose row it wants. An organization normally has a single live row,
    but a plan change publishes the new one before the old is retired.
    """
    collection = await _collection(_ENTITLEMENTS_CURRENT, tenant_id)
    query = {
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        **_live_status(datetime.now(timezone.utc)),
    }
    return await asyncio.to_thread(
        lambda: list(collection.find(query).sort("SubscriptionVersion", -1))
    )
