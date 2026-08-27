"""Pure period/allowance math for usage metering. No I/O -- callers pass in already-fetched
documents.

Known simplification: DST gap/ambiguous local times resolve via Python's default `fold=0`
semantics rather than an explicit nudge-forward search. Only matters for a subscription whose
billing anchor falls inside a one-hour DST transition window, twice a year, and shifts that
one boundary by at most an hour.
"""
import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# BillingInterval
INTERVAL_DAY, INTERVAL_WEEK, INTERVAL_MONTH, INTERVAL_YEAR = 0, 1, 2, 3
_INTERVAL_CODE = {INTERVAL_DAY: "D", INTERVAL_WEEK: "W", INTERVAL_MONTH: "M", INTERVAL_YEAR: "Y"}

# MeterResetPolicy
RESET_PERIODIC, RESET_NEVER, RESET_CARRY_FORWARD = 0, 1, 2

# SubscriptionStatus
STATUS_INCOMPLETE = 0
STATUS_INCOMPLETE_EXPIRED = 1
STATUS_TRIALING = 2
STATUS_ACTIVE = 3
STATUS_PAST_DUE = 4
STATUS_UNPAID = 5
STATUS_CANCELED = 6
LIVE_STATUSES = (STATUS_TRIALING, STATUS_ACTIVE, STATUS_PAST_DUE)

LIFETIME_PERIOD_KEY = "LIFETIME"
_MAX_INDEX_CORRECTIONS = 8


@dataclass(frozen=True)
class Schedule:
    interval: int
    interval_count: int
    anchor_instant_utc: datetime
    time_zone_id: str
    anchor_day_of_month: int
    anchor_minutes_from_midnight: int


@dataclass(frozen=True)
class TrialGrant:
    meter_key: str
    included_quantity: int


@dataclass(frozen=True)
class Trial:
    ends_at_utc: datetime
    grants: List[TrialGrant] = field(default_factory=list)


@dataclass(frozen=True)
class Meter:
    meter_key: str
    reset_policy: int
    included_quantity: int
    carry_forward_cap: Optional[int]
    overage_allowed: bool


@dataclass(frozen=True)
class Subscription:
    item_id: str
    tenant_id: str
    organization_id: str
    status: int
    created_at_utc: datetime
    usage_schedule: Optional[Schedule]
    trial: Optional[Trial]
    meters: List[Meter] = field(default_factory=list)


@dataclass(frozen=True)
class BillingPeriod:
    index: int
    start_utc: datetime
    end_utc: datetime
    key: str


def now_utc() -> datetime:
    """Naive UTC now -- matches what pymongo returns for stored datetimes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def period_key(interval: int, period_start_utc: datetime) -> str:
    code = _INTERVAL_CODE.get(interval, "U")
    return f"{code}{period_start_utc.strftime('%Y%m%dT%H%M%S')}Z"


def _find_timezone(tz_id: Optional[str]) -> Optional[ZoneInfo]:
    if not tz_id:
        return None
    try:
        return ZoneInfo(tz_id)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    return local_naive.replace(tzinfo=tz, fold=0).astimezone(timezone.utc).replace(tzinfo=None)


def _to_local(instant_utc: datetime, tz: ZoneInfo) -> datetime:
    return instant_utc.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def _month_boundary(schedule: Schedule, anchor_local: datetime, month_offset: int) -> datetime:
    total_months = anchor_local.year * 12 + (anchor_local.month - 1) + month_offset
    target_year, target_month0 = divmod(total_months, 12)
    target_month = target_month0 + 1
    day = min(schedule.anchor_day_of_month, calendar.monthrange(target_year, target_month)[1])
    return datetime(target_year, target_month, day)


def _boundary_of(schedule: Schedule, tz: ZoneInfo, anchor_local: datetime, index: int) -> datetime:
    offset = index * schedule.interval_count
    if schedule.interval == INTERVAL_DAY:
        local = datetime.combine(anchor_local.date() + timedelta(days=offset), datetime.min.time())
    elif schedule.interval == INTERVAL_WEEK:
        local = datetime.combine(anchor_local.date() + timedelta(days=offset * 7), datetime.min.time())
    elif schedule.interval == INTERVAL_MONTH:
        local = _month_boundary(schedule, anchor_local, offset)
    elif schedule.interval == INTERVAL_YEAR:
        local = _month_boundary(schedule, anchor_local, offset * 12)
    else:
        local = datetime.combine(anchor_local.date(), datetime.min.time())
    local = local + timedelta(minutes=schedule.anchor_minutes_from_midnight)
    return _to_utc(local, tz)


def _months_between(a: datetime, b: datetime) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _estimate_index(schedule: Schedule, tz: ZoneInfo, anchor_local: datetime, instant_utc: datetime) -> int:
    local = _to_local(instant_utc, tz)
    if schedule.interval == INTERVAL_DAY:
        elapsed = (local.date() - anchor_local.date()).days
    elif schedule.interval == INTERVAL_WEEK:
        elapsed = (local.date() - anchor_local.date()).days // 7
    elif schedule.interval == INTERVAL_MONTH:
        elapsed = _months_between(anchor_local, local)
    elif schedule.interval == INTERVAL_YEAR:
        elapsed = _months_between(anchor_local, local) // 12
    else:
        elapsed = 0
    return elapsed // schedule.interval_count


def _correct_index(schedule: Schedule, tz: ZoneInfo, anchor_local: datetime, instant_utc: datetime, estimate: int) -> int:
    index = estimate
    for _ in range(_MAX_INDEX_CORRECTIONS):
        if _boundary_of(schedule, tz, anchor_local, index) > instant_utc:
            index -= 1
            continue
        if _boundary_of(schedule, tz, anchor_local, index + 1) <= instant_utc:
            index += 1
            continue
        break
    return index


def try_get_period(schedule: Optional[Schedule], instant_utc: datetime) -> Optional[BillingPeriod]:
    """The billing period containing instant_utc. None if the schedule is invalid."""
    if schedule is None or schedule.interval_count < 1:
        return None
    tz = _find_timezone(schedule.time_zone_id)
    if tz is None:
        return None
    anchor_local = _to_local(schedule.anchor_instant_utc, tz)
    estimate = _estimate_index(schedule, tz, anchor_local, instant_utc)
    index = _correct_index(schedule, tz, anchor_local, instant_utc, estimate)
    start_utc = _boundary_of(schedule, tz, anchor_local, index)
    end_utc = _boundary_of(schedule, tz, anchor_local, index + 1)
    return BillingPeriod(index, start_utc, end_utc, period_key(schedule.interval, start_utc))


def get_meter_period(subscription: Subscription, meter: Meter, instant_utc: datetime) -> Optional[BillingPeriod]:
    """Never-reset meters get one lifetime window; everything else follows the schedule."""
    if meter.reset_policy == RESET_NEVER:
        return BillingPeriod(0, subscription.created_at_utc, datetime.max, LIFETIME_PERIOD_KEY)
    return try_get_period(subscription.usage_schedule, instant_utc)


def get_previous_meter_period(subscription: Subscription, meter: Meter, period: BillingPeriod) -> Optional[BillingPeriod]:
    """The window right before this one -- only meaningful for a carry-forward meter."""
    if meter.reset_policy != RESET_CARRY_FORWARD:
        return None
    return try_get_period(subscription.usage_schedule, period.start_utc - timedelta(microseconds=1))


def meter_base_allowance(subscription: Subscription, meter: Meter) -> int:
    """The plan's included quantity, or the trial's grant when one applies."""
    if subscription.status != STATUS_TRIALING or subscription.trial is None:
        return meter.included_quantity
    grant = next((g for g in subscription.trial.grants if g.meter_key == meter.meter_key), None)
    return grant.included_quantity if grant else meter.included_quantity


def meter_carried_in(
    subscription: Subscription,
    meter: Meter,
    previous_period: BillingPeriod,
    previous_counter: Optional[dict],
) -> int:
    """How much of the previous window's allowance rolls into this one."""
    if (
        meter.reset_policy != RESET_CARRY_FORWARD
        or subscription.status == STATUS_TRIALING
        or subscription.usage_schedule is None
        or previous_period.start_utc < subscription.usage_schedule.anchor_instant_utc
    ):
        return 0
    if subscription.trial is not None and previous_period.start_utc < subscription.trial.ends_at_utc:
        return 0

    if previous_counter is None:
        unused = meter.included_quantity
    else:
        limit_snapshot = previous_counter.get("LimitSnapshot")
        base = limit_snapshot if limit_snapshot is not None else meter.included_quantity
        unused = base - previous_counter.get("Balance", 0)

    if unused <= 0:
        return 0
    if meter.carry_forward_cap is not None:
        return min(unused, max(0, meter.carry_forward_cap))
    return unused


def meter_allowance_effective(counter: Optional[dict], computed: int) -> int:
    """The frozen per-window allowance if one exists yet, else the computed one."""
    if counter is not None and counter.get("LimitSnapshot") is not None:
        return counter["LimitSnapshot"]
    return computed
