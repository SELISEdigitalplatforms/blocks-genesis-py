"""blocks_genesis._subscription.context.SubscriptionUsageContext -- the ambient snapshot holder."""
import asyncio

import pytest

from blocks_genesis import UsageResult
from blocks_genesis._subscription.context import SubscriptionUsageContext


@pytest.fixture(autouse=True)
def _clear_context():
    SubscriptionUsageContext.clear()
    yield
    SubscriptionUsageContext.clear()


def _row(meter_key="ai-credits", allowed=True, remaining=499.0):
    return UsageResult(
        allowed=allowed, meter_key=meter_key, used=1.0,
        remaining=remaining, overage=0.0, replayed=False,
    )


def test_defaults_to_none_when_never_set():
    assert SubscriptionUsageContext.current() is None
    assert SubscriptionUsageContext.has_snapshot() is False


def test_set_and_read_back():
    snapshot = [_row()]
    SubscriptionUsageContext.set(snapshot)
    assert SubscriptionUsageContext.current() == snapshot
    assert SubscriptionUsageContext.has_snapshot() is True


def test_an_empty_snapshot_is_still_a_snapshot():
    # [] means "asked, nothing to report" -- distinct from None, "never asked or it failed".
    SubscriptionUsageContext.set([])
    assert SubscriptionUsageContext.current() == []
    assert SubscriptionUsageContext.has_snapshot() is True


def test_clear_returns_to_none():
    SubscriptionUsageContext.set([_row()])
    SubscriptionUsageContext.clear()
    assert SubscriptionUsageContext.current() is None
    assert SubscriptionUsageContext.has_snapshot() is False


def test_for_meter_finds_the_row():
    SubscriptionUsageContext.set([_row("messages"), _row("ai-credits", remaining=42.0)])
    found = SubscriptionUsageContext.for_meter("ai-credits")
    assert found is not None
    assert found.remaining == 42.0


def test_for_meter_is_none_for_an_absent_meter_and_an_absent_snapshot():
    SubscriptionUsageContext.set([_row("messages")])
    assert SubscriptionUsageContext.for_meter("tool-calls") is None
    SubscriptionUsageContext.clear()
    assert SubscriptionUsageContext.for_meter("messages") is None


@pytest.mark.asyncio
async def test_a_child_task_sees_what_was_set_before_it_started():
    SubscriptionUsageContext.set([_row("messages")])

    async def read():
        row = SubscriptionUsageContext.for_meter("messages")
        return row.meter_key if row else None

    assert await asyncio.create_task(read()) == "messages"


@pytest.mark.asyncio
async def test_a_child_task_setting_it_does_not_leak_back_to_the_parent():
    # Each task gets its own copy of the context, so a background task cannot overwrite the
    # request's snapshot -- the reason this is a ContextVar and not a module global.
    SubscriptionUsageContext.set([_row("messages")])

    async def overwrite():
        SubscriptionUsageContext.set([_row("tool-calls")])

    await asyncio.create_task(overwrite())
    current = SubscriptionUsageContext.current()
    assert [r.meter_key for r in current] == ["messages"]
