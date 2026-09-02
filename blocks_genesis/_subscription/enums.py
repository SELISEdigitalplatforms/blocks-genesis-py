"""Stored ints on SubscriptionUsageCurrent.SubscriptionStatus.

The writer lives outside this workspace, so this mapping is recorded rather than derived --
correct it here if a row ever contradicts it.
"""
from enum import IntEnum


class SubscriptionStatus(IntEnum):
    INCOMPLETE = 0
    TRIALING = 1
    ACTIVE = 2
    PAST_DUE = 3
    CANCELED = 4
    EXPIRED = 5
