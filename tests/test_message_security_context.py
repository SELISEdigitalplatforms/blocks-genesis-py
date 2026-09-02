"""A queue message's SecurityContext must not carry request-scoped context fields.

usage_snapshot holds pydantic UsageResult objects, which are not JSON-serializable -- and it
is stale by the time a consumer reads it. Leaving it in failed every send_to_consumer_async
made during a request that had fetched a snapshot, not just usage recording.
"""
import json

from blocks_genesis import UsageResult
from blocks_genesis._auth.blocks_context import TRANSIENT_CONTEXT_FIELDS, BlocksContext
from blocks_genesis._message.azure.azure_message_client import DateTimeEncoder, _wire_context


def _context_with_snapshot() -> BlocksContext:
    ctx = BlocksContext(tenant_id="t1", organization_id="default")
    ctx.usage_snapshot = [
        UsageResult(
            allowed=True, meter_key="ai-credits", used=1.0,
            remaining=499.0, overage=0.0, replayed=False,
        )
    ]
    return ctx


def test_usage_snapshot_is_declared_transient():
    assert "usage_snapshot" in TRANSIENT_CONTEXT_FIELDS


def test_azure_wire_context_drops_the_snapshot_and_serializes():
    wire = _wire_context(_context_with_snapshot())
    assert "usage_snapshot" not in wire
    assert json.loads(json.dumps(wire, cls=DateTimeEncoder))["tenant_id"] == "t1"


def test_rabbit_model_dump_drops_the_snapshot_and_serializes():
    dumped = _context_with_snapshot().model_dump(mode="json", exclude=TRANSIENT_CONTEXT_FIELDS)
    assert "usage_snapshot" not in dumped
    assert json.loads(json.dumps(dumped))["tenant_id"] == "t1"


def test_azure_wire_context_of_none_is_empty():
    assert _wire_context(None) == {}


def test_azure_wire_context_keeps_identity_fields():
    wire = _wire_context(_context_with_snapshot())
    assert wire["tenant_id"] == "t1"
    assert wire["organization_id"] == "default"
