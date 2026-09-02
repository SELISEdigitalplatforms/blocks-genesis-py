"""Stored ints on SubscriptionUsageCurrent.SubscriptionStatus.

The writer lives outside this workspace, so this mapping is recorded, not derived. Correct it
here if a row contradicts it -- blocks-utilities' own enum disagrees (Active = 3 there).
"""
from enum import IntEnum


class SubscriptionStatus(IntEnum):
    INCOMPLETE = 0
    TRIALING = 1
    ACTIVE = 2
    PAST_DUE = 3
    CANCELED = 4
    EXPIRED = 5
