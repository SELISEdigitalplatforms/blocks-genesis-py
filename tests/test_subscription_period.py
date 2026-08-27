"""blocks_genesis._subscription.period -- pure period/allowance math for usage metering."""
from datetime import datetime

from blocks_genesis._subscription import period as p


def _monthly_schedule(anchor=datetime(2026, 1, 1), day=1):
    return p.Schedule(
        interval=p.INTERVAL_MONTH,
        interval_count=1,
        anchor_instant_utc=anchor,
        time_zone_id="UTC",
        anchor_day_of_month=day,
        anchor_minutes_from_midnight=0,
    )


def _meter(reset_policy=p.RESET_PERIODIC, included=100, carry_cap=None):
    return p.Meter(
        meter_key="messages",
        reset_policy=reset_policy,
        included_quantity=included,
        carry_forward_cap=carry_cap,
        overage_allowed=True,
    )


def _subscription(schedule=None, status=p.STATUS_ACTIVE, trial=None):
    return p.Subscription(
        item_id="sub-1",
        tenant_id="t1",
        organization_id="org-1",
        status=status,
        created_at_utc=datetime(2026, 1, 1),
        usage_schedule=schedule or _monthly_schedule(),
        trial=trial,
    )


def test_try_get_period_places_instant_in_the_right_monthly_window():
    period = p.try_get_period(_monthly_schedule(), datetime(2026, 3, 15))
    assert period.start_utc == datetime(2026, 3, 1)
    assert period.end_utc == datetime(2026, 4, 1)
    assert period.key == "M20260301T000000Z"


def test_try_get_period_at_exact_boundary_belongs_to_the_new_period():
    period = p.try_get_period(_monthly_schedule(), datetime(2026, 3, 1))
    assert period.start_utc == datetime(2026, 3, 1)


def test_try_get_period_clamps_month_end_anchor():
    # Anchored on the 31st: the window opening in February clamps to the 28th (no 31st),
    # and the window after it returns to the 31st in March.
    schedule = _monthly_schedule(anchor=datetime(2026, 1, 31), day=31)
    period = p.try_get_period(schedule, datetime(2026, 3, 15))
    assert period.start_utc == datetime(2026, 2, 28)
    assert period.end_utc == datetime(2026, 3, 31)


def test_try_get_period_unknown_timezone_returns_none():
    schedule = p.Schedule(
        interval=p.INTERVAL_MONTH, interval_count=1, anchor_instant_utc=datetime(2026, 1, 1),
        time_zone_id="Not/AZone", anchor_day_of_month=1, anchor_minutes_from_midnight=0,
    )
    assert p.try_get_period(schedule, datetime(2026, 3, 1)) is None


def test_get_meter_period_never_reset_is_one_lifetime_window():
    sub = _subscription()
    meter = _meter(reset_policy=p.RESET_NEVER)
    period = p.get_meter_period(sub, meter, datetime(2027, 6, 1))
    assert period.key == p.LIFETIME_PERIOD_KEY
    assert period.start_utc == sub.created_at_utc


def test_meter_base_allowance_uses_plan_quantity_outside_trial():
    sub = _subscription(status=p.STATUS_ACTIVE)
    assert p.meter_base_allowance(sub, _meter(included=100)) == 100


def test_meter_base_allowance_uses_trial_grant_when_trialing():
    trial = p.Trial(ends_at_utc=datetime(2026, 2, 1), grants=[p.TrialGrant(meter_key="messages", included_quantity=20)])
    sub = _subscription(status=p.STATUS_TRIALING, trial=trial)
    assert p.meter_base_allowance(sub, _meter(included=100)) == 20


def test_meter_base_allowance_falls_back_to_plan_quantity_when_no_grant_for_meter():
    trial = p.Trial(ends_at_utc=datetime(2026, 2, 1), grants=[])
    sub = _subscription(status=p.STATUS_TRIALING, trial=trial)
    assert p.meter_base_allowance(sub, _meter(included=100)) == 100


def test_meter_carried_in_passes_on_unused_allowance():
    sub = _subscription()
    meter = _meter(reset_policy=p.RESET_CARRY_FORWARD, included=100)
    previous_period = p.BillingPeriod(0, datetime(2026, 1, 1), datetime(2026, 2, 1), "M20260101T000000Z")
    previous_counter = {"Balance": 30, "LimitSnapshot": 100}
    assert p.meter_carried_in(sub, meter, previous_period, previous_counter) == 70


def test_meter_carried_in_bounded_by_carry_forward_cap():
    sub = _subscription()
    meter = _meter(reset_policy=p.RESET_CARRY_FORWARD, included=100, carry_cap=10)
    previous_period = p.BillingPeriod(0, datetime(2026, 1, 1), datetime(2026, 2, 1), "M20260101T000000Z")
    previous_counter = {"Balance": 30, "LimitSnapshot": 100}  # 70 unused, capped to 10
    assert p.meter_carried_in(sub, meter, previous_period, previous_counter) == 10


def test_meter_carried_in_nothing_before_the_schedule_anchor():
    sub = _subscription()  # anchor = 2026-01-01
    meter = _meter(reset_policy=p.RESET_CARRY_FORWARD, included=100)
    previous_period = p.BillingPeriod(-1, datetime(2025, 12, 1), datetime(2026, 1, 1), "M20251201T000000Z")
    assert p.meter_carried_in(sub, meter, previous_period, None) == 0


def test_meter_allowance_effective_prefers_frozen_snapshot():
    assert p.meter_allowance_effective({"LimitSnapshot": 42}, computed=100) == 42
    assert p.meter_allowance_effective(None, computed=100) == 100
    assert p.meter_allowance_effective({"LimitSnapshot": None}, computed=100) == 100
