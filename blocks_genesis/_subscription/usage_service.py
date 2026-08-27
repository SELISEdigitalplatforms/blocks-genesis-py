"""Current usage, read straight from Mongo -- no HTTP call. Recording usage is out of scope
here; blocks-agents handles that through its own API.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from blocks_genesis._subscription import period as period_mod
from blocks_genesis._subscription import repository
from blocks_genesis._subscription.models import UsageResult

logger = logging.getLogger(__name__)


def _describe(meter: period_mod.Meter, balance: int, allowance: int) -> UsageResult:
    return UsageResult(
        allowed=True,
        meter_key=meter.meter_key,
        used=balance,
        remaining=max(0, allowance - balance),
        overage=max(0, balance - allowance),
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
        """Every meter's balance for the current period. [] means no live subscription;
        None means the read itself failed (log only, don't raise)."""
        if not tenant_id or not organization_id:
            logger.error("get_usage_current: tenant_id and organization_id are required")
            return None

        try:
            subscription = await repository.get_live_subscription(tenant_id, organization_id)
        except Exception:
            logger.exception("get_usage_current: Mongo read failed")
            return None

        if subscription is None:
            return []

        now = period_mod.now_utc()
        results: List[UsageResult] = []
        for meter in subscription.meters:
            result = await cls._describe_meter(subscription, meter, now)
            if result is None:
                logger.error(
                    "get_usage_current: could not resolve period for meter=%s sub=%s",
                    meter.meter_key, subscription.item_id,
                )
                continue
            results.append(result)
        return results

    @classmethod
    async def _describe_meter(cls, subscription: period_mod.Subscription, meter: period_mod.Meter, now: datetime) -> Optional[UsageResult]:
        period = period_mod.get_meter_period(subscription, meter, now)
        if period is None:
            return None
        counter = await repository.get_counter(
            subscription.tenant_id,
            repository.counter_id(subscription.item_id, meter.meter_key, period.key),
        )
        allowance = await cls._effective_allowance(subscription, meter, period, counter)
        balance = counter.get("Balance", 0) if counter else 0
        return _describe(meter, balance, allowance)

    @classmethod
    async def _effective_allowance(
        cls,
        subscription: period_mod.Subscription,
        meter: period_mod.Meter,
        period: period_mod.BillingPeriod,
        counter: Optional[Dict[str, Any]],
    ) -> int:
        if counter is not None and counter.get("LimitSnapshot") is not None:
            return counter["LimitSnapshot"]
        return await cls._opening_allowance(subscription, meter, period)

    @classmethod
    async def _opening_allowance(cls, subscription: period_mod.Subscription, meter: period_mod.Meter, period: period_mod.BillingPeriod) -> int:
        base = period_mod.meter_base_allowance(subscription, meter)
        if meter.reset_policy != period_mod.RESET_CARRY_FORWARD:
            return base
        previous_period = period_mod.get_previous_meter_period(subscription, meter, period)
        if previous_period is None:
            return base
        previous_counter = await repository.get_counter(
            subscription.tenant_id,
            repository.counter_id(subscription.item_id, meter.meter_key, previous_period.key),
        )
        return base + period_mod.meter_carried_in(subscription, meter, previous_period, previous_counter)
