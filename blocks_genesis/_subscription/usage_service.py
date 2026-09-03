"""Current usage, read straight from Mongo. The SubscriptionUsageCurrent read model already
carries the computed allowance per meter, so there is no period math on this side.
"""
import logging
from typing import Any, Dict, List, Optional

from blocks_genesis._subscription import repository
from blocks_genesis._subscription.models import UsageResult

logger = logging.getLogger(__name__)


def _number(value: Any) -> float:
    """A stored quantity as a float. Decimal128 (what the meter writes now) and any plain
    number both work; nothing truncates, and an unreadable value reads as 0."""
    if value is None:
        return 0.0
    to_decimal = getattr(value, "to_decimal", None)
    if to_decimal is not None:
        value = to_decimal()
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Unreadable usage quantity %r; treating as 0", value)
        return 0.0


MAX_QUANTITY_SCALE = 6


def _scale(value: Any) -> int:
    """The meter's decimal places, clamped to what the API accepts. Unreadable reads as 0."""
    try:
        return max(0, min(MAX_QUANTITY_SCALE, int(value)))
    except (TypeError, ValueError):
        return 0


def _to_result(doc: Dict[str, Any]) -> UsageResult:
    used = _number(doc.get("Used"))
    included = _number(doc.get("Included"))
    overage_allowed = bool(doc.get("OverageAllowed", True))
    scale = _scale(doc.get("QuantityScale"))
    return UsageResult(
        allowed=used <= included or overage_allowed,
        meter_key=doc.get("MeterKey") or "",
        used=used,
        remaining=_number(doc.get("Remaining")),
        overage=_number(doc.get("Overage")),
        replayed=False,
        quantity_scale=scale,
        is_fraction_allowed=scale > 0,
    )


class SubscriptionUsageService:
    """Stateless: Mongo access via DbContext, which the app/worker wires up at startup."""

    @classmethod
    async def get_usage_current(
        cls,
        *,
        tenant_id: str,
        organization_id: str,
    ) -> Optional[List[UsageResult]]:
        """Every meter's balance for the current period.

        [] means no live subscription; None means the read failed.
        """
        if not tenant_id or not organization_id:
            logger.error("get_usage_current: tenant_id and organization_id are required")
            return None

        try:
            docs = await repository.get_current_usage_docs(tenant_id, organization_id)
        except Exception:
            logger.exception("get_usage_current: Mongo read failed")
            return None

        return [_to_result(doc) for doc in docs]
