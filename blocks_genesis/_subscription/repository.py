"""Reads SubscriptionUsageCurrent -- a pre-computed read model, one row per meter."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from blocks_genesis._database.db_context import DbContext
from blocks_genesis._subscription.enums import SubscriptionStatus

logger = logging.getLogger(__name__)

_USAGE_CURRENT = "SubscriptionUsageCurrent"


async def _collection(name: str, tenant_id: str):
    provider = DbContext.get_provider()
    return await provider.get_collection(name, tenant_id)


async def get_current_usage_docs(tenant_id: str, organization_id: str) -> List[Dict[str, Any]]:
    """One row per meter for the current period. Active subscriptions only."""
    collection = await _collection(_USAGE_CURRENT, tenant_id)
    now = datetime.now(timezone.utc)
    cursor = collection.find({
        "TenantId": tenant_id,
        "OrganizationId": organization_id,
        "SubscriptionStatus": int(SubscriptionStatus.ACTIVE),
        "PeriodStartUtc": {"$lte": now},
        "PeriodEndUtc": {"$gt": now},
    })
    return list(cursor)
