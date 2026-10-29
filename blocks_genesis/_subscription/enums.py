"""Stored ints on the subscription read models.

Mirrors blocks-utilities, which writes the fields. Values are explicit because they are
stored -- keep the two in step.
"""
from enum import IntEnum


class SubscriptionStatus(IntEnum):
    INCOMPLETE = 0
    INCOMPLETE_EXPIRED = 1
    TRIALING = 2
    ACTIVE = 3
    PAST_DUE = 4
    UNPAID = 5
    CANCELED = 6


class UsageWindow(IntEnum):
    """A pace limit's window inside the billing period."""

    HOUR = 0
    DAY = 1
    WEEK = 2


class SubLimitBehaviour(IntEnum):
    """What a reached pace limit does: refuse the use, or accept it and report it as over."""

    REFUSE = 0
    THROTTLE = 1


class EntitlementLimitKind(IntEnum):
    """Stored ints on a plan's entitlement. Boolean is "on or off, nothing to count"."""

    BOOLEAN = 0
    COUNT = 1
    UNLIMITED = 2
