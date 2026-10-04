"""A queue message's SecurityContext must not carry the usage snapshot.

It holds non-serializable rows and is stale by the time a consumer reads it. Now that it lives
in its own holder rather than on BlocksContext, it cannot reach the wire -- these hold that line.

Both transports build the payload with `create_sanitized_for_transport`, so there is one
place for this to go wrong rather than two.
"""
import json

from blocks_genesis import UsageResult
from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._message.azure.azure_message_client import DateTimeEncoder
from blocks_genesis._subscription.context import SubscriptionUsageContext


def _snapshot():
    return [
        UsageResult(
            allowed=True, meter_key="ai-credits", used=1.0,
            remaining=499.0, overage=0.0, replayed=False,
        )
    ]


def _context() -> BlocksContext:
    return BlocksContext(tenant_id="t1", organization_id="default")


def test_blocks_context_has_no_usage_snapshot_field():
    # Regression guard: putting it back reintroduces the non-serializable field on the wire
    # model, and the exclusion list that had to strip it.
    assert "usage_snapshot" not in BlocksContext.model_fields


def test_wire_context_serializes_while_a_snapshot_is_live():
    SubscriptionUsageContext.set(_snapshot())
    try:
        wire = BlocksContextManager.create_sanitized_for_transport(_context())
        assert "usage_snapshot" not in wire
        assert json.loads(json.dumps(wire, cls=DateTimeEncoder))["TenantId"] == "t1"
    finally:
        SubscriptionUsageContext.clear()


def test_model_dump_serializes_while_a_snapshot_is_live():
    SubscriptionUsageContext.set(_snapshot())
    try:
        dumped = _context().model_dump(mode="json")
        assert "usage_snapshot" not in dumped
        assert json.loads(json.dumps(dumped))["tenant_id"] == "t1"
    finally:
        SubscriptionUsageContext.clear()


def test_wire_context_of_none_is_empty():
    assert BlocksContextManager.create_sanitized_for_transport(None) == {}


def test_wire_context_keeps_identity_fields():
    wire = BlocksContextManager.create_sanitized_for_transport(_context())
    assert wire["TenantId"] == "t1"
    assert wire["OrganizationId"] == "default"
