"""Current usage, read straight from Mongo -- no HTTP call anywhere in this path.

Backed by the SubscriptionUsageCurrent read model, which already carries the computed
allowance per meter, so there is no period or carry-forward math on this side.
"""
import logging
from typing import Any, Dict, List, Optional

from blocks_genesis._subscription import repository
from blocks_genesis._subscription.models import UsageResult

logger = logging.getLogger(__name__)


def _to_result(doc: Dict[str, Any]) -> UsageResult:
    used = int(doc.get("Used") or 0)
    included = int(doc.get("Included") or 0)
    overage_allowed = bool(doc.get("OverageAllowed", True))
    return UsageResult(
        allowed=used <= included or overage_allowed,
        meter_key=doc.get("MeterKey") or "",
        used=used,
        remaining=int(doc.get("Remaining") or 0),
        overage=int(doc.get("Overage") or 0),
        replayed=False,
    )


class SubscriptionUsageService:
    """Stateless -- no HTTP, no session, nothing to initialize. Direct Mongo access via
    DbContext, which the app/worker already wires up before this is ever called."""

    @classmethod
    async def get_usage_current(
        cls,
        *,
        tenant_id: str,
        organization_id: str,
    ) -> Optional[List[UsageResult]]:
        """Every meter's balance for the current period. [] means no live subscription (or no
        meter has a current-period row yet); None means the read itself failed."""
        if not tenant_id or not organization_id:
            logger.error("get_usage_current: tenant_id and organization_id are required")
            return None

        try:
            docs = await repository.get_current_usage_docs(tenant_id, organization_id)
        except Exception:
            logger.exception("get_usage_current: Mongo read failed")
            return None

        return [_to_result(doc) for doc in docs]
