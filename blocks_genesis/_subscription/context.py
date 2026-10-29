"""Ambient holder for the current unit of work's usage snapshot.

Set once by `subscription_usage_snapshot()` before the handler runs. Kept off `BlocksContext`:
that model is identity and travels in every queue message, where a request-scoped snapshot has
no place. Same shape as `_delegation`'s holders.
"""

from contextvars import ContextVar
from typing import List, Optional

from blocks_genesis._subscription.models import UsageResult

_usage_snapshot_var: ContextVar[Optional[List[UsageResult]]] = ContextVar(
    "blocks_subscription_usage_snapshot", default=None
)


class SubscriptionUsageContext:
    """The usage snapshot for the current logical flow."""

    @staticmethod
    def set(snapshot: Optional[List[UsageResult]]) -> None:
        _usage_snapshot_var.set(snapshot)

    @staticmethod
    def current() -> Optional[List[UsageResult]]:
        """The snapshot, or None when it was never fetched or the lookup failed."""
        return _usage_snapshot_var.get()

    @staticmethod
    def has_snapshot() -> bool:
        return _usage_snapshot_var.get() is not None

    @staticmethod
    def clear() -> None:
        _usage_snapshot_var.set(None)

    @staticmethod
    def for_meter(meter_key: str) -> Optional[UsageResult]:
        """One meter's row. None when unknown -- an absent meter is not a denial."""
        snapshot = _usage_snapshot_var.get()
        if not snapshot:
            return None
        return next((u for u in snapshot if u.meter_key == meter_key), None)
