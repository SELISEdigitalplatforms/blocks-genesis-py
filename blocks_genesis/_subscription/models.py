from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from blocks_genesis._subscription.enums import EntitlementLimitKind


class UsageSubLimit(BaseModel):
    """One pace limit on a meter and how much of its current window is spent.

    Story: a plan allows 2000 credits a month and 100 a day. On a day with 60 used, this
    reads window=DAY, quantity=100, used=60, remaining=40, exceeded=False.

    `window` and `behaviour` are the stored ints -- see UsageWindow and SubLimitBehaviour.
    A rolling window has no end; it slides, so `used` is an upper bound between recordings.
    """

    window: int = 0
    window_count: int = 1
    rolling: bool = False
    behaviour: int = 0
    quantity: float = 0.0
    used: float = 0.0
    remaining: float = 0.0
    exceeded: bool = False
    window_start_utc: Optional[datetime] = None
    window_end_utc: Optional[datetime] = None


class UsageResult(BaseModel):
    """Quantities are floats -- the meter stores them as Decimal128 and they may be fractional."""

    allowed: bool
    meter_key: str
    used: float
    remaining: float
    overage: float
    replayed: bool
    # Decimal places the meter accepts: 0 (the default) is whole numbers only, 2 allows 550.55.
    quantity_scale: int = 0
    # Derived from quantity_scale, for callers that only need the yes/no.
    is_fraction_allowed: bool = False
    # False when the plan stops the meter at its allowance. A charge recorded without
    # enforce still goes past it, so a caller that must not bill overage caps it itself.
    overage_allowed: bool = True
    # The meter's pace limits as last recorded. Empty on a meter with none, or before any
    # recording has reported them.
    sub_limits: List[UsageSubLimit] = Field(default_factory=list)


class Entitlement(BaseModel):
    """One key a plan grants. Keys are opaque: the product invents them, the subscription
    module stores and counts against them, and neither side reads the other's meaning
    into the string."""

    key: str
    limit_kind: int = int(EntitlementLimitKind.BOOLEAN)
    # The cap when the kind is a count. Never a balance -- that is the usage row.
    limit: Optional[float] = None
    meter_key: Optional[str] = None
    unit_label: Optional[str] = None


class SubscriptionEntitlements(BaseModel):
    """What one subscription's plan grants.

    `has_subscription` false means no live subscription was found, which is not the same
    as a plan that grants nothing.
    """

    has_subscription: bool = False
    subscription_id: str = ""
    plan_id: str = ""
    plan_code: str = ""
    # The plan's own feature bag, verbatim. Stored and served, never interpreted here.
    features_json: Optional[str] = None
    entitlements: List[Entitlement] = Field(default_factory=list)

    def keys(self) -> List[str]:
        return [e.key for e in self.entitlements]

    def grants(self, key: str) -> bool:
        return any(e.key == key for e in self.entitlements)


class ScopedEntitlements(BaseModel):
    """The plan terms read for each side of a caller.

    `member` is keyed by user id, so the terms name whose they are. A member absent from
    the map was never asked for; None against a key means that read failed.
    """

    organization: Optional[SubscriptionEntitlements] = None
    member: Dict[str, Optional[SubscriptionEntitlements]] = Field(default_factory=dict)


class MemberSides(BaseModel):
    """One or more members' own answers, keyed by user id.

    The same two maps `UsageSnapshot` holds, on their own, for a caller that wants a few
    members' data and has no snapshot to carry. Each map keeps the three states a snapshot
    keeps: a key absent was never asked about, None is a read that failed, and [] or an
    empty plan is an answer.
    """

    usage: Dict[str, Optional[List[UsageResult]]] = Field(default_factory=dict)
    entitlements: Dict[str, Optional[SubscriptionEntitlements]] = Field(default_factory=dict)


class UsageSnapshot(BaseModel):
    """One caller's subscription in one object: the organization's balances, each member's
    own, and the plan terms behind them.

    `organization` keeps the convention the single-list read has always had: None is a
    failed read, [] is no live subscription.

    `member` is keyed by user id, so a reader never has to infer whose balances these are.
    That matters because the member asked about is not always the person signed in -- a
    Composio trigger, a scheduled job or a named payer all name someone else. Three states
    per key, and they must not be collapsed:

    - the key is absent -- nobody asked about that member
    - the key maps to None -- asked, and the read failed
    - the key maps to [] -- asked, and that member has no subscription rows of their own
    """

    organization: Optional[List[UsageResult]] = None
    member: Dict[str, Optional[List[UsageResult]]] = Field(default_factory=dict)
    entitlements: ScopedEntitlements = Field(default_factory=ScopedEntitlements)

    def rows_for(self, user_id: str) -> Optional[List[UsageResult]]:
        """One member's balances, or None for both "never asked" and "the read failed" --
        a caller that has to tell those apart reads `member` itself."""
        return self.member.get(user_id)
