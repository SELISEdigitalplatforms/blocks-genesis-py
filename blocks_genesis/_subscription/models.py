from typing import List, Optional

from pydantic import BaseModel


class Entitlement(BaseModel):
    key: str
    allowed: bool
    reason: str  # opaque string, e.g. "Allowed", "NoSubscription", "SubscriptionNotActive" -- not a closed enum
    limit_kind: Optional[str] = None  # Boolean | Count | Unlimited
    limit: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None


class EntitlementsSnapshot(BaseModel):
    has_subscription: bool
    status: Optional[str] = None
    plan_code: Optional[str] = None
    entitlements: List[Entitlement] = []


class UsageResult(BaseModel):
    allowed: bool
    meter_key: str
    used: int
    remaining: int
    overage: int
    replayed: bool
